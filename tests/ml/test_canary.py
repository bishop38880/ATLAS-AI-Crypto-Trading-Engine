"""Tests for ML Canary Deployment — Phase 3.

Covers:
    - Deterministic hash cycle selection (10%/50%).
    - Shadow comparison for classification and regression.
    - Canary promotion and rollback logic.
    - Rolling Sharpe and max drawdown calculations.
    - Integration test: bad model auto-rollback.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.ml.model_registry import (
    CanaryState,
    ModelRegistry,
    ModelRegistryEntry,
)
from atlas.orchestrator.ml_canary import (
    CanaryOrchestrator,
    compare_shadow_outputs,
    compute_max_drawdown,
    compute_rolling_sharpe,
    deterministic_canary_hash,
    should_rollback,
)
from atlas.shared.config import CanaryConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def canary_config() -> CanaryConfig:
    """Provide a CanaryConfig with fast defaults for testing."""
    return CanaryConfig(
        enabled=True,
        shadow_cycles_required=500,
        classification_agreement_threshold=0.95,
        regression_mae_tolerance=0.10,
        canary_sharpe_degradation_pct=0.10,
        canary_drawdown_degradation_pct=0.10,
        canary_10pct_duration_days=7,
        canary_50pct_duration_days=7,
        rolling_window_hours=4,
    )


@pytest.fixture
def mock_registry() -> MagicMock:
    """Provide a mock ModelRegistry."""
    registry = MagicMock(spec=ModelRegistry)
    registry.register_model = AsyncMock()
    registry.get_model = AsyncMock(return_value=None)
    registry.get_live_model = AsyncMock(return_value=None)
    registry.get_models_by_state = AsyncMock(return_value=[])
    registry.update_state = AsyncMock()
    registry.update_metrics = AsyncMock()
    registry.decrement_shadow_cycles = AsyncMock(return_value=499)
    return registry


@pytest.fixture
def orchestrator(
    mock_registry: MagicMock,
    canary_config: CanaryConfig,
) -> CanaryOrchestrator:
    """Provide a CanaryOrchestrator with mocked dependencies."""
    return CanaryOrchestrator(
        registry=mock_registry,
        config=canary_config,
    )


# ---------------------------------------------------------------------------
# Test 1: Deterministic hash — 10%
# ---------------------------------------------------------------------------


class TestDeterministicHash10Pct:
    """Verify ~10% of cycles are selected with threshold=10."""

    def test_approximately_10_percent_selected(self) -> None:
        """Run 1000 asset+timestamp combos, expect ~10% selected."""
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        selected = 0
        total = 1000

        for i in range(total):
            asset = "BTCUSDT" if i % 2 == 0 else "ETHUSDT"
            ts = base_time + timedelta(minutes=i)
            if deterministic_canary_hash(asset, ts, 10):
                selected += 1

        pct = selected / total
        assert 0.05 <= pct <= 0.18, (
            "Expected ~10%% selection, got {:.1%}".format(pct)
        )


# ---------------------------------------------------------------------------
# Test 2: Deterministic hash — 50%
# ---------------------------------------------------------------------------


class TestDeterministicHash50Pct:
    """Verify ~50% of cycles are selected with threshold=50."""

    def test_approximately_50_percent_selected(self) -> None:
        """Run 1000 asset+timestamp combos, expect ~50% selected."""
        base_time = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        selected = 0
        total = 1000

        for i in range(total):
            asset = "BTCUSDT" if i % 3 == 0 else "SOLUSDT"
            ts = base_time + timedelta(minutes=i)
            if deterministic_canary_hash(asset, ts, 50):
                selected += 1

        pct = selected / total
        assert 0.40 <= pct <= 0.60, (
            "Expected ~50%% selection, got {:.1%}".format(pct)
        )


# ---------------------------------------------------------------------------
# Test 3: Hash reproducibility
# ---------------------------------------------------------------------------


class TestHashReproducibility:
    """Same input always produces the same result."""

    def test_same_input_same_output(self) -> None:
        """Call deterministic_canary_hash 100 times with same args."""
        ts = datetime(2026, 6, 15, 12, 30, tzinfo=timezone.utc)
        results = [
            deterministic_canary_hash("BTCUSDT", ts, 10)
            for _ in range(100)
        ]
        assert all(r == results[0] for r in results)


# ---------------------------------------------------------------------------
# Test 4: Shadow cycles decrement
# ---------------------------------------------------------------------------


class TestShadowCyclesDecrement:
    """Shadow cycle counter decrements on each cycle."""

    @pytest.mark.asyncio
    async def test_decrement_500_to_499(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """After one shadow cycle, remaining should be 499."""
        mock_registry.decrement_shadow_cycles.return_value = 499

        state = await orchestrator.run_shadow_cycle(
            model_name="test_model",
            version="1.0",
            live_output="bullish",
            shadow_output="bullish",
            task_type="classification",
        )

        mock_registry.decrement_shadow_cycles.assert_awaited_once_with(
            "test_model", "1.0",
        )
        assert state == CanaryState.SHADOW


# ---------------------------------------------------------------------------
# Test 5: Shadow auto-promote after 500 cycles with agreement
# ---------------------------------------------------------------------------


class TestShadowAutoPromote:
    """After 500 agreeing cycles, model auto-promotes to canary_10pct."""

    @pytest.mark.asyncio
    async def test_auto_promote_on_zero_remaining(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """When shadow_cycles_remaining hits 0, promote to canary_10pct."""
        mock_registry.decrement_shadow_cycles.return_value = 0

        # Seed agreement history so the agreement check passes
        key = "test_model:2.0"
        orchestrator._shadow_agreements[key] = [True] * 499

        state = await orchestrator.run_shadow_cycle(
            model_name="test_model",
            version="2.0",
            live_output="bullish",
            shadow_output="bullish",
            task_type="classification",
        )

        assert state == CanaryState.CANARY_10PCT
        mock_registry.update_state.assert_awaited_once_with(
            "test_model", "2.0", CanaryState.CANARY_10PCT,
        )


# ---------------------------------------------------------------------------
# Test 6: Shadow no promote on disagreement
# ---------------------------------------------------------------------------


class TestShadowNoPromoteOnDisagreement:
    """Disagreement prevents promotion even after 500 cycles."""

    @pytest.mark.asyncio
    async def test_no_promote_with_disagreements(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """If agreement rate is below threshold, stay in shadow."""
        mock_registry.decrement_shadow_cycles.return_value = 0

        # Seed low agreement rate (50% — well below 95%)
        key = "test_model:3.0"
        orchestrator._shadow_agreements[key] = (
            [True] * 250 + [False] * 250
        )

        state = await orchestrator.run_shadow_cycle(
            model_name="test_model",
            version="3.0",
            live_output="bullish",
            shadow_output="bearish",
            task_type="classification",
        )

        assert state == CanaryState.SHADOW
        mock_registry.update_state.assert_not_awaited()


# ---------------------------------------------------------------------------
# Test 7: Rollback on Sharpe degradation
# ---------------------------------------------------------------------------


class TestRollbackOnSharpeDegradation:
    """Canary model with 15% Sharpe drop triggers rollback."""

    def test_sharpe_degradation_triggers_rollback(self) -> None:
        """15% Sharpe degradation with 10% tolerance → rollback."""
        result = should_rollback(
            canary_sharpe=0.85,
            live_sharpe=1.0,
            canary_dd=0.05,
            live_dd=0.05,
            tolerance_pct=0.10,
        )
        assert result is True

    def test_within_tolerance_no_rollback(self) -> None:
        """5% Sharpe degradation with 10% tolerance → no rollback."""
        result = should_rollback(
            canary_sharpe=0.95,
            live_sharpe=1.0,
            canary_dd=0.05,
            live_dd=0.05,
            tolerance_pct=0.10,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Test 8: Rollback on drawdown
# ---------------------------------------------------------------------------


class TestRollbackOnDrawdown:
    """Excessive drawdown relative to live triggers rollback."""

    def test_excessive_drawdown_triggers_rollback(self) -> None:
        """Canary drawdown 50% higher than live → rollback."""
        result = should_rollback(
            canary_sharpe=1.0,
            live_sharpe=1.0,
            canary_dd=0.15,
            live_dd=0.10,
            tolerance_pct=0.10,
        )
        assert result is True

    def test_similar_drawdown_no_rollback(self) -> None:
        """Canary drawdown within tolerance → no rollback."""
        result = should_rollback(
            canary_sharpe=1.0,
            live_sharpe=1.0,
            canary_dd=0.105,
            live_dd=0.10,
            tolerance_pct=0.10,
        )
        assert result is False


# ---------------------------------------------------------------------------
# Test 9: Promote canary_10pct → canary_50pct
# ---------------------------------------------------------------------------


class TestPromote10To50:
    """After 7 days within tolerance, canary_10pct → canary_50pct."""

    @pytest.mark.asyncio
    async def test_promote_after_7_days(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """7-day-old canary_10pct with good perf → canary_50pct."""
        start_time = datetime.now(timezone.utc) - timedelta(days=8)
        entry = ModelRegistryEntry(
            model_name="test_model",
            version="4.0",
            canary_state=CanaryState.CANARY_10PCT,
            canary_start_time=start_time,
        )
        mock_registry.get_model.return_value = entry

        key = "test_model:4.0"
        # Provide enough returns for Sharpe computation
        orchestrator._canary_returns[key] = [0.001] * 50
        orchestrator._live_returns[key] = [0.001] * 50

        state = await orchestrator.evaluate_canary("test_model", "4.0")
        assert state == CanaryState.CANARY_50PCT
        mock_registry.update_state.assert_awaited_once_with(
            "test_model", "4.0", CanaryState.CANARY_50PCT,
        )


# ---------------------------------------------------------------------------
# Test 10: Promote canary_50pct → live
# ---------------------------------------------------------------------------


class TestPromote50ToLive:
    """After 7 more days within tolerance, canary_50pct → live."""

    @pytest.mark.asyncio
    async def test_promote_after_7_days(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """7-day-old canary_50pct with good perf → live."""
        start_time = datetime.now(timezone.utc) - timedelta(days=8)
        entry = ModelRegistryEntry(
            model_name="test_model",
            version="5.0",
            canary_state=CanaryState.CANARY_50PCT,
            canary_start_time=start_time,
        )
        mock_registry.get_model.return_value = entry
        mock_registry.get_live_model.return_value = None

        key = "test_model:5.0"
        orchestrator._canary_returns[key] = [0.001] * 50
        orchestrator._live_returns[key] = [0.001] * 50

        state = await orchestrator.evaluate_canary("test_model", "5.0")
        assert state == CanaryState.LIVE


# ---------------------------------------------------------------------------
# Test 11: New model defaults to shadow
# ---------------------------------------------------------------------------


class TestNewModelDefaultsShadow:
    """register_model() creates model in shadow state."""

    def test_default_state_is_shadow(self) -> None:
        """ModelRegistryEntry defaults to shadow state."""
        entry = ModelRegistryEntry(
            model_name="new_model",
            version="1.0",
        )
        assert entry.canary_state == CanaryState.SHADOW
        assert entry.shadow_cycles_remaining == 500


# ---------------------------------------------------------------------------
# Test 12: Rollback latency under 5 minutes
# ---------------------------------------------------------------------------


class TestRollbackLatency:
    """Simulated rollback completes in <5 minutes."""

    @pytest.mark.asyncio
    async def test_rollback_completes_quickly(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """Rollback state transition should be near-instant."""
        entry = ModelRegistryEntry(
            model_name="latency_model",
            version="1.0",
            canary_state=CanaryState.CANARY_10PCT,
            canary_start_time=datetime.now(timezone.utc) - timedelta(days=1),
        )
        mock_registry.get_model.return_value = entry

        key = "latency_model:1.0"
        # Bad canary: negative returns
        orchestrator._canary_returns[key] = [-0.05] * 50
        orchestrator._live_returns[key] = [0.01] * 50

        start = time.monotonic()
        state = await orchestrator.evaluate_canary(
            "latency_model", "1.0",
        )
        elapsed = time.monotonic() - start

        assert state == CanaryState.SHADOW
        assert elapsed < 300, (
            "Rollback took {:.1f}s, must be <300s".format(elapsed)
        )


# ---------------------------------------------------------------------------
# Test 13: Integration — bad model auto-rollback
# ---------------------------------------------------------------------------


class TestIntegrationBadModelRollback:
    """Deploy a deliberately worse model and verify auto-rollback."""

    @pytest.mark.asyncio
    async def test_bad_model_is_rolled_back(
        self,
        orchestrator: CanaryOrchestrator,
        mock_registry: MagicMock,
    ) -> None:
        """Bad model with consistently negative returns → rollback."""
        entry = ModelRegistryEntry(
            model_name="bad_model",
            version="666",
            canary_state=CanaryState.CANARY_10PCT,
            canary_start_time=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        mock_registry.get_model.return_value = entry

        key = "bad_model:666"

        # Simulate 4 hours of cycles (2 per hour)
        for i in range(8):
            await orchestrator.record_cycle_return(
                "bad_model", "666",
                canary_return=-0.03,
                live_return=0.01,
            )

        state = await orchestrator.evaluate_canary("bad_model", "666")

        assert state == CanaryState.SHADOW
        mock_registry.update_state.assert_awaited_with(
            "bad_model", "666", CanaryState.SHADOW,
        )


# ---------------------------------------------------------------------------
# Shadow comparison unit tests
# ---------------------------------------------------------------------------


class TestShadowComparison:
    """Unit tests for compare_shadow_outputs."""

    def test_classification_agreement(self) -> None:
        """Identical classification outputs agree."""
        assert compare_shadow_outputs(
            "bullish", "bullish", "classification",
        ) is True

    def test_classification_disagreement(self) -> None:
        """Different classification outputs disagree."""
        assert compare_shadow_outputs(
            "bullish", "bearish", "classification",
        ) is False

    def test_classification_list_agreement(self) -> None:
        """List outputs with >95% agreement pass."""
        live = ["bullish"] * 100
        shadow = ["bullish"] * 96 + ["bearish"] * 4
        assert compare_shadow_outputs(
            live, shadow, "classification", 0.95,
        ) is True

    def test_classification_list_disagreement(self) -> None:
        """List outputs with <95% agreement fail."""
        live = ["bullish"] * 100
        shadow = ["bullish"] * 90 + ["bearish"] * 10
        assert compare_shadow_outputs(
            live, shadow, "classification", 0.95,
        ) is False

    def test_regression_within_tolerance(self) -> None:
        """Regression output within 10% MAE passes."""
        assert compare_shadow_outputs(
            100.0, 95.0, "regression", mae_tolerance=0.10,
        ) is True

    def test_regression_outside_tolerance(self) -> None:
        """Regression output outside 10% MAE fails."""
        assert compare_shadow_outputs(
            100.0, 80.0, "regression", mae_tolerance=0.10,
        ) is False


# ---------------------------------------------------------------------------
# Performance metric helpers
# ---------------------------------------------------------------------------


class TestPerformanceMetrics:
    """Tests for Sharpe and drawdown calculations."""

    def test_rolling_sharpe_positive(self) -> None:
        """Positive returns with variance produce positive Sharpe."""
        returns = [0.01, 0.02, 0.005, 0.015, 0.01, 0.02, 0.008, 0.012,
                   0.01, 0.02, 0.005, 0.015, 0.01, 0.02, 0.008, 0.012,
                   0.01, 0.02, 0.005, 0.015]
        sharpe = compute_rolling_sharpe(returns, window_hours=4)
        assert sharpe > 0

    def test_rolling_sharpe_zero_on_insufficient(self) -> None:
        """Insufficient data returns 0.0 Sharpe."""
        returns = [0.01]
        sharpe = compute_rolling_sharpe(returns, window_hours=4)
        assert sharpe == 0.0

    def test_max_drawdown_no_loss(self) -> None:
        """Monotonically increasing returns have zero drawdown."""
        returns = [0.01] * 10
        dd = compute_max_drawdown(returns)
        assert dd == 0.0

    def test_max_drawdown_with_loss(self) -> None:
        """Returns with a dip produce non-zero drawdown."""
        returns = [0.10, 0.05, -0.20, 0.05, 0.01]
        dd = compute_max_drawdown(returns)
        assert dd > 0.0

    def test_max_drawdown_empty(self) -> None:
        """Empty returns produce zero drawdown."""
        assert compute_max_drawdown([]) == 0.0
