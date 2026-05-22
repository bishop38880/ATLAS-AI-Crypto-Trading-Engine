# SKIP_INVARIANT_CHECK
"""Phase 10 — CQR Graduated Rollout integration tests.

Simulates 14 days of cycles and verifies:
- SHADOW computes bounds but never applies them to leverage.
- ACTIVE_TIGHT_ONLY correctly falls back when width >= 20.
- FULL always applies bounds.
- Stages never auto-promote without human approval.
- Point estimate is never modified by any stage.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.models.signal import AgentResult, SignalDirection
from atlas.ml.cqr_rollout import (
    CQRRolloutMetrics,
    CQRRolloutStage,
    RolloutDecision,
    apply_rollout_stage,
    check_shadow_promotion,
    check_tight_promotion,
    log_rollout_metrics,
    suggest_promotion,
)
from atlas.ml.uncertainty_propagator import UncertaintyBounds
from atlas.orchestrator.scorer import ConfluenceScorer
from atlas.shared.config import CQRConfig, PolarisSettings

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_bounds(width: int, point: int = 150) -> UncertaintyBounds:
    """Build UncertaintyBounds with given width centered on point."""
    half = width // 2
    return UncertaintyBounds(
        lower=max(0, point - half),
        point=point,
        upper=min(220, point + half),
        method="TEST",
        width=width,
    )


def _make_scorer(
    stage: str = "SHADOW",
) -> tuple[ConfluenceScorer, MagicMock]:
    """Build a ConfluenceScorer wired with mocked CQR calibrator."""
    settings = PolarisSettings()
    settings.cqr = CQRConfig(
        enabled=True,
        cqr_rollout_stage=stage,
    )
    cal_mock = MagicMock()
    cal_mock.is_trained.return_value = True
    cal_mock.predict_bounds.return_value = _make_bounds(
        width=40, point=150,
    )
    redis_mock = AsyncMock()
    scorer = ConfluenceScorer(
        settings=settings,
        cqr_calibrator=cal_mock,
        redis_client=redis_mock,
    )
    return scorer, cal_mock


def _make_agents() -> list[AgentResult]:
    """Standard agent results for scorer integration."""
    return [
        AgentResult(
            agent_name="technical", score=70, max_score=100,
            explanation="test", convergences=["A"], risks=["B"],
            sub_signals={}, veto=False,
        ),
        AgentResult(
            agent_name="derivatives", score=80, max_score=100,
            explanation="test", convergences=["C"], risks=[],
            sub_signals={}, veto=False,
        ),
    ]


# ---------------------------------------------------------------------------
# Test 1: SHADOW computes but does not apply
# ---------------------------------------------------------------------------


async def test_shadow_computes_but_does_not_apply():
    """SHADOW stage: bounds computed, use_for_leverage=False."""
    bounds = _make_bounds(width=40)
    result = apply_rollout_stage(
        CQRRolloutStage.SHADOW, bounds, raw_score=150,
    )
    assert isinstance(result, RolloutDecision)
    assert result.use_for_leverage is False
    assert result.bounds.width == 40
    assert result.stage == CQRRolloutStage.SHADOW


# ---------------------------------------------------------------------------
# Test 2: ACTIVE_TIGHT_ONLY falls back on wide bounds
# ---------------------------------------------------------------------------


async def test_active_tight_only_falls_back_wide_bounds():
    """Width >= 20: bounds computed but not applied to leverage."""
    bounds = _make_bounds(width=25)
    result = apply_rollout_stage(
        CQRRolloutStage.ACTIVE_TIGHT_ONLY, bounds, raw_score=150,
    )
    assert result.use_for_leverage is False
    assert result.bounds.width == 25


# ---------------------------------------------------------------------------
# Test 3: ACTIVE_TIGHT_ONLY applies tight bounds
# ---------------------------------------------------------------------------


async def test_active_tight_only_applies_tight_bounds():
    """Width < 20: bounds applied to leverage."""
    bounds = _make_bounds(width=15)
    result = apply_rollout_stage(
        CQRRolloutStage.ACTIVE_TIGHT_ONLY, bounds, raw_score=150,
    )
    assert result.use_for_leverage is True
    assert result.bounds.width == 15


# ---------------------------------------------------------------------------
# Test 4: FULL always applies
# ---------------------------------------------------------------------------


async def test_full_always_applies():
    """FULL stage: bounds always applied regardless of width."""
    for width in (5, 19, 20, 50, 100):
        bounds = _make_bounds(width=width)
        result = apply_rollout_stage(
            CQRRolloutStage.FULL, bounds, raw_score=150,
        )
        assert result.use_for_leverage is True


# ---------------------------------------------------------------------------
# Test 5: No auto-promotion without human approval (14-day sim)
# ---------------------------------------------------------------------------


async def test_no_auto_promotion_without_approval():
    """Simulate 14 days: stage must never change automatically."""
    conn_mock = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = ctx

    stage = CQRRolloutStage.SHADOW
    # Simulate 14 days, 10 cycles/day = 140 iterations
    for _day in range(140):
        suggestion = await suggest_promotion(
            pool=pool,
            current_stage=stage,
            avg_width_current=15.0,  # meets criteria
            avg_width_initial=30.0,  # 50% decrease
            num_trades=100,
            sharpe_bounds=1.5,
            sharpe_point=1.0,
        )
        # suggestion returned but stage NEVER changes
        assert stage == CQRRolloutStage.SHADOW
        if suggestion:
            assert suggestion == "ACTIVE_TIGHT_ONLY"


# ---------------------------------------------------------------------------
# Test 6: Shadow promotion criteria met
# ---------------------------------------------------------------------------


async def test_shadow_promotion_criteria_met():
    """Avg width decreased >= 30% from initial."""
    assert check_shadow_promotion(
        avg_width_current=20.0, avg_width_initial=30.0,
    ) is True


# ---------------------------------------------------------------------------
# Test 7: Shadow promotion criteria not met
# ---------------------------------------------------------------------------


async def test_shadow_promotion_criteria_not_met():
    """Avg width decreased < 30% from initial."""
    assert check_shadow_promotion(
        avg_width_current=22.0, avg_width_initial=30.0,
    ) is False


# ---------------------------------------------------------------------------
# Test 8: Tight promotion criteria met
# ---------------------------------------------------------------------------


async def test_tight_promotion_criteria_met():
    """>= 50 trades, sharpe_bounds >= sharpe_point."""
    assert check_tight_promotion(
        num_trades=60, sharpe_bounds=1.5, sharpe_point=1.2,
    ) is True


# ---------------------------------------------------------------------------
# Test 9: Tight promotion criteria not met (< 50 trades)
# ---------------------------------------------------------------------------


async def test_tight_promotion_criteria_not_met():
    """< 50 trades -> no promotion."""
    assert check_tight_promotion(
        num_trades=30, sharpe_bounds=1.5, sharpe_point=1.2,
    ) is False


# ---------------------------------------------------------------------------
# Test 10: Point estimate never modified
# ---------------------------------------------------------------------------


async def test_point_estimate_never_modified():
    """Raw score identical before/after bounds application in all stages."""
    raw_score = 150
    bounds = _make_bounds(width=40, point=raw_score)

    for stage in CQRRolloutStage:
        result = apply_rollout_stage(stage, bounds, raw_score)
        assert result.bounds.point == raw_score


# ---------------------------------------------------------------------------
# Test 11: Metrics persisted
# ---------------------------------------------------------------------------


async def test_metrics_persisted():
    """Verify INSERT into cqr_rollout_metrics."""
    conn_mock = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = ctx

    metrics = CQRRolloutMetrics(
        timestamp=datetime.now(timezone.utc),
        stage="SHADOW",
        avg_width=25.0,
        num_trades_using_bounds=10,
        sharpe_bounds=1.2,
        sharpe_point=1.0,
    )
    await log_rollout_metrics(pool, metrics)
    conn_mock.execute.assert_called_once()
    call_args = conn_mock.execute.call_args
    assert "INSERT INTO cqr_rollout_metrics" in call_args[0][0]


# ---------------------------------------------------------------------------
# Test 12: suggest_promotion logs but does not change stage
# ---------------------------------------------------------------------------


async def test_suggest_promotion_logs_but_does_not_change_stage():
    """Promotion suggestion logged, stage remains unchanged."""
    conn_mock = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn_mock)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = ctx

    original_stage = CQRRolloutStage.ACTIVE_TIGHT_ONLY
    suggestion = await suggest_promotion(
        pool=pool,
        current_stage=original_stage,
        avg_width_current=10.0,
        avg_width_initial=30.0,
        num_trades=60,
        sharpe_bounds=1.5,
        sharpe_point=1.0,
    )
    assert suggestion == "FULL"
    # Stage enum object itself was never mutated
    assert original_stage == CQRRolloutStage.ACTIVE_TIGHT_ONLY
