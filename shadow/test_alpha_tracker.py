"""Tests for ShadowAlphaTracker — S3-P11 quality gate.

All asyncpg and Redis operations are mocked.
Test floor: 8 tests required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.models.signal import (
    CategoryScores,
    ExitReason,
    SignalDecision,
    SignalOutput,
    TradeOutcome,
)
from atlas.models.telemetry import TelemetryEvent
from shadow.alpha_tracker import (
    AlphaTrackingRecord,
    MetricSimulation,
    PromotionReadinessSummary,
    ShadowAlphaTracker,
    _determine_decision,
)
from shadow.collector import ShadowMetrics


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings() -> MagicMock:
    """Create a minimal PolarisSettings mock."""
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    settings.postgres_url = "postgresql://localhost/atlas"
    return settings


def _mock_pool() -> AsyncMock:
    """Create a mock asyncpg.Pool with context manager support."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _mock_redis() -> AsyncMock:
    """Create a mock redis.asyncio.Redis."""
    return AsyncMock()


def _make_tracker() -> tuple[ShadowAlphaTracker, AsyncMock, AsyncMock]:
    """Create ShadowAlphaTracker with mocked dependencies."""
    settings = _make_settings()
    pool = _mock_pool()
    redis_client = _mock_redis()
    tracker = ShadowAlphaTracker(
        settings=settings,
        asyncpg_pool=pool,
        redis_client=redis_client,
    )
    return tracker, pool, redis_client


def _make_shadow_metrics(
    vwap_deviation: float = 0.03,
    vwap_direction: str = "ABOVE",
    ob_imbalance: float | None = 0.4,
    ob_persistent: bool = True,
    ob_spoof: bool = False,
    sr_proximity: float = 0.5,
    atr_move: float = 1.0,
) -> ShadowMetrics:
    """Create ShadowMetrics with configurable values."""
    return ShadowMetrics(
        asset="BTCUSDT",
        cycle_timestamp=datetime.now(timezone.utc),
        vwap_deviation=vwap_deviation,
        vwap_deviation_direction=vwap_direction,  # type: ignore[arg-type]
        ob_imbalance=ob_imbalance,
        ob_imbalance_persistent=ob_persistent,
        ob_potential_spoof_detected=ob_spoof,
        sr_proximity_atr_normalised=sr_proximity,
        nearest_sr_level=Decimal("100000"),
        nearest_sr_type="RESISTANCE",
        atr_normalised_move=atr_move,
        atr_14=Decimal("1500"),
    )


def _make_signal(
    decision: SignalDecision = SignalDecision.BUY,
    score: int = 72,
    raw_score: int = 158,
) -> SignalOutput:
    """Create a minimal SignalOutput for testing."""
    from atlas.models.signal import ActionBlock

    now = datetime.now(timezone.utc)

    # BUY/STRONG_BUY need an ActionBlock
    action = None
    if decision in (
        SignalDecision.BUY,
        SignalDecision.STRONG_BUY,
        SignalDecision.SELL,
        SignalDecision.STRONG_SELL,
    ):
        side = "buy" if decision in (
            SignalDecision.BUY, SignalDecision.STRONG_BUY,
        ) else "sell"
        action = ActionBlock(
            side=side,
            order_type="limit",
            price=Decimal("100000"),
            stop_loss=Decimal("95000"),
            take_profit=Decimal("110000"),
        )

    return SignalOutput(
        decision=decision,
        asset="BTCUSDT",
        score=score,
        confidence=Decimal("0.75"),
        category_scores=CategoryScores(
            technical=30, derivatives=25, onchain=15,
            sentiment=10, whale=10, liquidation=10,
            regime=10, funding=10, news_macro=10,
            correlation=10, context=18,
            total=158,
        ),
        telemetry=TelemetryEvent(
            cycle_id="test-cycle",
            cycle_latency_ms=100.0,
            agent_count=10,
        ),
        expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        raw_confluence_score=raw_score,
        action=action,
    )


def _make_outcome(
    pnl_pct: Decimal = Decimal("2.5"),
) -> TradeOutcome:
    """Create a TradeOutcome for testing."""
    return TradeOutcome(
        signal_id="test-signal-123",
        pnl_pct=pnl_pct,
        exit_reason=ExitReason.TAKE_PROFIT,
    )


# ---------------------------------------------------------------------------
# Test 1: record_trade_outcome → simulation_results contains all 4 metrics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_simulation_results_contain_all_four_metrics() -> None:
    """record_trade_outcome returns all 4 shadow metric simulations."""
    tracker, _, _ = _make_tracker()
    signal = _make_signal()
    shadow = _make_shadow_metrics()
    outcome = _make_outcome()

    record = await tracker.record_trade_outcome(
        signal_id="sig-001",
        original_signal=signal,
        original_decision="BUY",
        shadow_metrics_at_entry=shadow,
        outcome=outcome,
    )

    expected_keys = {
        "vwap_deviation", "ob_imbalance",
        "sr_proximity", "atr_normalised_move",
    }
    assert set(record.simulation_results.keys()) == expected_keys
    assert isinstance(record, AlphaTrackingRecord)


# ---------------------------------------------------------------------------
# Test 2: VWAP deviation aligned with LONG, |dev|>2% → shadow_pts=8
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vwap_aligned_long_high_deviation_scores_8() -> None:
    """VWAP deviation >2%, ABOVE, signal is BUY → 8 pts."""
    tracker, _, _ = _make_tracker()
    shadow = _make_shadow_metrics(
        vwap_deviation=0.03,
        vwap_direction="ABOVE",
    )
    signal = _make_signal(decision=SignalDecision.BUY)

    record = await tracker.record_trade_outcome(
        signal_id="sig-002",
        original_signal=signal,
        original_decision="BUY",
        shadow_metrics_at_entry=shadow,
        outcome=_make_outcome(),
    )

    vwap_sim = record.simulation_results["vwap_deviation"]
    assert vwap_sim.shadow_pts_awarded == 8


# ---------------------------------------------------------------------------
# Test 3: VWAP deviation against signal direction → shadow_pts=0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vwap_against_signal_direction_scores_0() -> None:
    """VWAP deviation >2%, BELOW, signal is BUY → 0 pts (against)."""
    tracker, _, _ = _make_tracker()
    shadow = _make_shadow_metrics(
        vwap_deviation=0.03,
        vwap_direction="BELOW",
    )
    signal = _make_signal(decision=SignalDecision.BUY)

    record = await tracker.record_trade_outcome(
        signal_id="sig-003",
        original_signal=signal,
        original_decision="BUY",
        shadow_metrics_at_entry=shadow,
        outcome=_make_outcome(),
    )

    vwap_sim = record.simulation_results["vwap_deviation"]
    assert vwap_sim.shadow_pts_awarded == 0


# ---------------------------------------------------------------------------
# Test 4: At least one metric flips decision → any_metric_changed=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metric_flips_decision_sets_any_changed_true() -> None:
    """Signal near decision boundary + shadow pts → decision changes."""
    tracker, _, _ = _make_tracker()

    # Score just below BUY threshold (68): score=54, raw=118
    # Adding 8 VWAP pts: raw=126, score=round(126/220*100)=57 → HOLD (changed!)
    signal = _make_signal(
        decision=SignalDecision.HOLD,
        score=54,
        raw_score=118,
    )
    shadow = _make_shadow_metrics(
        vwap_deviation=0.03,
        vwap_direction="ABOVE",
    )

    record = await tracker.record_trade_outcome(
        signal_id="sig-004",
        original_signal=signal,
        original_decision="NO_POSITION",
        shadow_metrics_at_entry=shadow,
        outcome=_make_outcome(),
    )

    # At least one metric should have changed the decision from NO_POSITION
    assert record.any_metric_would_have_changed_decision is True


# ---------------------------------------------------------------------------
# Test 5: Promotion readiness with <200 trades → ready_for_shap=False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promotion_readiness_under_200_not_ready() -> None:
    """Fewer than 200 trades → ready_for_shap_analysis is False."""
    tracker, _, _ = _make_tracker()

    # Mock asyncpg to return 50 rows
    mock_rows = [
        {
            "simulation_json": {
                "vwap_deviation": {
                    "would_have_changed_decision": True,
                    "was_shadow_better": True,
                },
            },
            "any_changed": True,
        }
        for _ in range(50)
    ]

    with patch.object(
        tracker, "_query_metric_rows",
        new_callable=AsyncMock, return_value=mock_rows,
    ):
        summary = await tracker.get_promotion_readiness_summary(
            "vwap_deviation",
        )

    assert summary.total_trades_analyzed == 50
    assert summary.ready_for_shap_analysis is False


# ---------------------------------------------------------------------------
# Test 6: Promotion readiness with >=200 trades → ready_for_shap=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_promotion_readiness_200_plus_ready() -> None:
    """200+ trades → ready_for_shap_analysis is True."""
    tracker, _, _ = _make_tracker()

    mock_rows = [
        {
            "simulation_json": {
                "vwap_deviation": {
                    "would_have_changed_decision": False,
                    "was_shadow_better": None,
                },
            },
            "any_changed": False,
        }
        for _ in range(250)
    ]

    with patch.object(
        tracker, "_query_metric_rows",
        new_callable=AsyncMock, return_value=mock_rows,
    ):
        summary = await tracker.get_promotion_readiness_summary(
            "vwap_deviation",
        )

    assert summary.total_trades_analyzed == 250
    assert summary.ready_for_shap_analysis is True


# ---------------------------------------------------------------------------
# Test 7: store_record asyncpg raises → ERROR logged, no exception escapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_record_asyncpg_error_suppressed() -> None:
    """asyncpg failure in store_record → ERROR logged, no raise."""
    tracker, pool, _ = _make_tracker()

    # Make asyncpg raise
    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(side_effect=RuntimeError("DB down"))
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn_mock)

    signal = _make_signal()
    shadow = _make_shadow_metrics()
    outcome = _make_outcome()

    record = await tracker.record_trade_outcome(
        signal_id="sig-007",
        original_signal=signal,
        original_decision="BUY",
        shadow_metrics_at_entry=shadow,
        outcome=outcome,
    )

    # Should not raise — errors are logged internally
    with patch("shadow.alpha_tracker.logger") as mock_logger:
        await tracker.store_record(record)
        mock_logger.error.assert_called_once()


# ---------------------------------------------------------------------------
# Test 8: outcome=None (open trade) → was_shadow_better=None for all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_trade_was_shadow_better_none() -> None:
    """outcome=None → was_shadow_better is None for every simulation."""
    tracker, _, _ = _make_tracker()
    signal = _make_signal()
    shadow = _make_shadow_metrics()

    record = await tracker.record_trade_outcome(
        signal_id="sig-008",
        original_signal=signal,
        original_decision="BUY",
        shadow_metrics_at_entry=shadow,
        outcome=None,
    )

    for sim in record.simulation_results.values():
        assert sim.was_shadow_better is None, (
            "Open trade should have was_shadow_better=None, "
            "got {} for {}".format(sim.was_shadow_better, sim.metric_name)
        )
    assert record.outcome_pnl_pct is None
