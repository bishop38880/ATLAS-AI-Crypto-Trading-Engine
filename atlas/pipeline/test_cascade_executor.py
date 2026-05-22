"""Tests for CascadeSetupDetector (S2-P2).

All Redis and HYDRA reads are mocked. Seven tests covering:
  1. All preconditions met → STAGE1_FIRED
  2. Active probe, price touches cluster → STAGE2_FIRED
  3. Expired probe → PROBE_EXPIRED
  4. Preconditions A/B met, C not met → NO_ACTION
  5. Re-entry after Stage 2 → new Stage 1 fires (no cooldown)
  6. Redis degraded on probe read → NO_ACTION
  7. HYDRA returns no clusters → Precondition B = False, NO_ACTION
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone, timedelta
from decimal import Decimal

from atlas.models.signal import SignalOutput, SignalDecision, CategoryScores
from atlas.models.telemetry import TelemetryEvent
from atlas.pipeline.pubsub import RedisSignalPublisher
from atlas.pipeline.cascade_executor import (
    CascadeSetupDetector,
    CascadeExecutor,
    CascadeProbe,
)
from atlas.shared.config import PolarisSettings


def _dummy_signal(score: int, derivatives: int, regime: int) -> SignalOutput:
    ts = datetime.now(timezone.utc)
    return SignalOutput(
        signal_id="test_id",
        timestamp=ts,
        decision=SignalDecision.HOLD,
        asset="BTCUSDT",
        timeframe="30m",
        action=None,
        expires_at=ts + timedelta(hours=1),
        reasoning_summary="",
        key_convergences=[],
        key_risks=[],
        is_cascade_triggered=False,
        hydra_event_id=None,
        score=score,
        confidence=Decimal("0.9"),
        category_scores=CategoryScores(
            derivatives=derivatives,
            regime=regime,
            technical=score - derivatives - regime,
            total=score,
        ),
        contributing_graph_paths=["regime"] if regime > 0 else [],
        telemetry=TelemetryEvent(cycle_id="test", cycle_latency_ms=10.0, agent_count=5),
        pipeline_confidence=Decimal("0.9"),
    )


@pytest.fixture
def settings() -> PolarisSettings:
    return PolarisSettings()


@pytest.fixture
def mock_redis() -> AsyncMock:
    mock = AsyncMock()
    mock.get.return_value = None
    return mock


@pytest.fixture
def mock_hydra_redis() -> AsyncMock:
    """HYDRA Redis DB 1 — returns clusters by default."""
    mock = AsyncMock()
    # Default: one cluster at 59000
    import msgspec
    mock.get.return_value = msgspec.json.encode([{"price_band": "59000.0", "density_score": "80", "side": "long"}])
    return mock


@pytest.fixture
def mock_publisher() -> AsyncMock:
    return AsyncMock(spec=RedisSignalPublisher)


@pytest.fixture
def detector(settings, mock_redis, mock_publisher, mock_hydra_redis):
    return CascadeSetupDetector(settings, mock_redis, mock_publisher, mock_hydra_redis)


# ── Test 1 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stage1_fires_no_active_probe(detector, mock_redis, mock_publisher):
    """All three preconditions met, no active probe → STAGE1_FIRED."""
    signal = _dummy_signal(score=85, derivatives=45, regime=10)
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("60000.0"), Decimal("59000.0")
    )

    assert res.action == "STAGE1_FIRED"
    assert res.probe_active is True
    assert res.preconditions_met == {"A": True, "B": True, "C": True}

    mock_redis.get.assert_called_once()
    mock_redis.setex.assert_called_once()
    mock_publisher.publish.assert_called_once()

    args, _kwargs = mock_publisher.publish.call_args
    assert args[1]["stage"] == 1
    assert args[1]["size_pct"] == 0.25


# ── Test 2 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stage2_fires_active_probe(detector, mock_redis, mock_publisher):
    """Active probe, price touches cluster → STAGE2_FIRED, probe cleared."""
    ts = datetime.now(timezone.utc)
    probe = CascadeProbe(
        asset="BTCUSDT",
        stage1_entry_price=Decimal("60000.0"),
        liquidation_cluster_price=Decimal("59000.0"),
        stage1_signal_score=85,
        created_at=ts,
        expires_at=ts + timedelta(hours=4),
    )
    mock_redis.get.return_value = probe.model_dump_json()

    signal = _dummy_signal(score=50, derivatives=0, regime=0)
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("59000.0"), Decimal("59000.0")
    )

    assert res.action == "STAGE2_FIRED"
    assert res.probe_active is False
    mock_redis.delete.assert_called_once()
    mock_publisher.publish.assert_called_once()

    args, _kwargs = mock_publisher.publish.call_args
    assert args[1]["stage"] == 2
    assert args[1]["size_pct"] == 0.75


# ── Test 3 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_probe_expires(detector, mock_redis, mock_publisher):
    """Probe with expires_at 5h in past → PROBE_EXPIRED, key deleted."""
    ts = datetime.now(timezone.utc) - timedelta(hours=5)
    probe = CascadeProbe(
        asset="BTCUSDT",
        stage1_entry_price=Decimal("60000.0"),
        liquidation_cluster_price=Decimal("59000.0"),
        stage1_signal_score=85,
        created_at=ts,
        expires_at=ts + timedelta(hours=4),
    )
    mock_redis.get.return_value = probe.model_dump_json()

    signal = _dummy_signal(score=50, derivatives=0, regime=0)
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("59500.0"), Decimal("59000.0")
    )

    assert res.action == "PROBE_EXPIRED"
    mock_redis.delete.assert_called_once()
    mock_publisher.publish.assert_not_called()


# ── Test 4 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_preconditions_not_met(detector, mock_redis, mock_publisher):
    """Preconditions A=True, B=True, C=False → NO_ACTION."""
    signal = _dummy_signal(score=85, derivatives=45, regime=0)
    signal = signal.model_copy(update={"contributing_graph_paths": []})
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("60000.0"), Decimal("59000.0")
    )

    assert res.action == "NO_ACTION"
    assert res.preconditions_met == {"A": True, "B": True, "C": False}
    mock_redis.setex.assert_not_called()
    mock_publisher.publish.assert_not_called()


# ── Test 5 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_immediate_reentry(detector, mock_redis, mock_publisher):
    """Stage 2 fires → next call with preconditions met → new Stage 1."""
    ts = datetime.now(timezone.utc)
    probe = CascadeProbe(
        asset="BTCUSDT",
        stage1_entry_price=Decimal("60000.0"),
        liquidation_cluster_price=Decimal("59000.0"),
        stage1_signal_score=85,
        created_at=ts,
        expires_at=ts + timedelta(hours=4),
    )
    mock_redis.get.return_value = probe.model_dump_json()
    signal = _dummy_signal(score=85, derivatives=45, regime=10)

    res1 = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("59000.0"), Decimal("58000.0")
    )
    assert res1.action == "STAGE2_FIRED"
    mock_redis.delete.assert_called_once()

    # Probe cleared — next call evaluates Stage 1.
    mock_redis.get.return_value = None
    mock_redis.delete.reset_mock()
    mock_publisher.publish.reset_mock()

    res2 = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("59000.0"), Decimal("58000.0")
    )
    assert res2.action == "STAGE1_FIRED"
    mock_redis.setex.assert_called_once()
    mock_publisher.publish.assert_called_once()


# ── Test 6 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_redis_degraded_read(detector, mock_redis, mock_publisher):
    """Redis unavailable on probe read → DEGRADED log, NO_ACTION."""
    mock_redis.get.side_effect = Exception("Redis timeout")

    signal = _dummy_signal(score=50, derivatives=0, regime=0)
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("60000.0"), Decimal("59000.0")
    )

    assert res.action == "NO_ACTION"


# ── Test 7 ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hydra_no_clusters(detector, mock_redis, mock_publisher, mock_hydra_redis):
    """HYDRA returns no clusters → Precondition B = False, NO_ACTION."""
    import msgspec
    mock_hydra_redis.get.return_value = msgspec.json.encode([])

    signal = _dummy_signal(score=85, derivatives=45, regime=10)
    res = await detector.evaluate_cascade(
        "BTCUSDT", signal, Decimal("60000.0"), None
    )

    assert res.action == "NO_ACTION"
    assert res.preconditions_met["A"] is True
    assert res.preconditions_met["B"] is False
    assert res.preconditions_met["C"] is True
    mock_redis.setex.assert_not_called()
    mock_publisher.publish.assert_not_called()


# ── Alias test ──────────────────────────────────────────────────

def test_cascade_executor_alias():
    """CascadeExecutor is a backwards-compatible alias."""
    assert CascadeExecutor is CascadeSetupDetector
