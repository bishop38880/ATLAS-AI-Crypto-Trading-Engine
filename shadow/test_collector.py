"""Tests for ShadowCollector — S3-P10 quality gate.

All Redis and asyncpg operations are mocked.
Test floor: 8 tests required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.models.ohlcv import OHLCVCandle
from shadow.collector import (
    ShadowCollector,
    ShadowMetrics,
    _calculate_atr_14,
    _vwap_deviation_from_window,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_candle(
    ts_offset: int,
    open_: str,
    high: str,
    low: str,
    close: str,
    volume: str = "100",
) -> OHLCVCandle:
    """Factory for OHLCVCandle with offset-based timestamp."""
    return OHLCVCandle(
        timestamp=datetime(2026, 1, 1, 0, ts_offset, tzinfo=timezone.utc),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def _make_50_candles() -> list[OHLCVCandle]:
    """Generate 50 candles with a known VWAP-computable pattern.

    Candles 0-48: price hovers around 100, volume 100.
    Candle 49: close at 110 (above VWAP) to create measurable deviation.
    """
    candles: list[OHLCVCandle] = []
    for i in range(49):
        candles.append(
            _make_candle(
                ts_offset=i,
                open_="100",
                high="101",
                low="99",
                close="100",
                volume="100",
            )
        )
    # Final candle — close well above VWAP
    candles.append(
        _make_candle(
            ts_offset=49,
            open_="100",
            high="111",
            low="109",
            close="110",
            volume="100",
        )
    )
    return candles


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


def _make_settings() -> MagicMock:
    """Create a minimal PolarisSettings mock."""
    settings = MagicMock()
    settings.redis_url = "redis://localhost:6379/0"
    settings.postgres_url = "postgresql://localhost/atlas"
    return settings


def _make_collector() -> tuple[ShadowCollector, AsyncMock, AsyncMock]:
    """Create ShadowCollector with mocked dependencies."""
    settings = _make_settings()
    redis_client = _mock_redis()
    pool = _mock_pool()
    collector = ShadowCollector(
        settings=settings,
        redis_client=redis_client,
        asyncpg_pool=pool,
    )
    return collector, redis_client, pool


# ---------------------------------------------------------------------------
# Test 1: 50 valid candles → vwap_deviation matches hand-computed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vwap_deviation_50_candles() -> None:
    """50 candles with known pattern → deviation is positive and ABOVE."""
    collector, _, _ = _make_collector()
    candles = _make_50_candles()

    # Hand-compute expected VWAP
    # Candles 0-48: tp = (101 + 99 + 100)/3 = 100.0, vol = 100
    # Candle 49:    tp = (111 + 109 + 110)/3 = 110.0, vol = 100
    # sum_tp_vol = 49 * 100 * 100 + 1 * 110 * 100 = 490_000 + 11_000 = 501_000
    # sum_vol = 50 * 100 = 5_000
    # vwap = 501_000 / 5_000 = 100.2
    # deviation = (110 - 100.2) / 100.2 = 9.8 / 100.2 ≈ 0.09780...

    dev, direction = collector._calculate_vwap_deviation(candles)

    assert direction == "ABOVE"
    assert dev > 0.09, "Expected positive deviation ~0.098"
    assert dev < 0.11, "Expected deviation less than 0.11"

    # Verify exact Decimal math
    three = Decimal("3")
    expected_tp_49 = (Decimal("111") + Decimal("109") + Decimal("110")) / three
    expected_tp_rest = (Decimal("101") + Decimal("99") + Decimal("100")) / three
    sum_tp_vol = expected_tp_rest * Decimal("100") * 49 + expected_tp_49 * Decimal("100")
    sum_vol = Decimal("5000")
    expected_vwap = sum_tp_vol / sum_vol
    expected_dev = float((Decimal("110") - expected_vwap) / expected_vwap)
    assert abs(dev - expected_dev) < 1e-10, (
        "Deviation mismatch: got={}, expected={}".format(dev, expected_dev)
    )


# ---------------------------------------------------------------------------
# Test 2: < 10 candles → neutral
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_vwap_deviation_insufficient_candles() -> None:
    """Fewer than 10 candles → vwap_deviation == 0.0, direction NEUTRAL."""
    collector, _, _ = _make_collector()
    candles = [
        _make_candle(i, "100", "101", "99", "100")
        for i in range(5)
    ]

    dev, direction = collector._calculate_vwap_deviation(candles)

    assert dev == 0.0
    assert direction == "NEUTRAL"


# ---------------------------------------------------------------------------
# Test 3: 3 OB snapshots all bid-heavy → persistent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ob_imbalance_persistent_bid_heavy() -> None:
    """3 consecutive bid-heavy snapshots → imbalance populated, persistent=True."""
    collector, _, _ = _make_collector()
    snapshots = [
        {"bid_vol_top10": 1000, "ask_vol_top10": 500},
        {"bid_vol_top10": 1100, "ask_vol_top10": 600},
        {"bid_vol_top10": 900, "ask_vol_top10": 400},
    ]

    imb, persistent, spoof = collector._calculate_ob_imbalance(snapshots)

    assert imb is not None
    assert imb > 0, "Bid-heavy should give positive imbalance"
    assert persistent is True
    assert spoof is False


# ---------------------------------------------------------------------------
# Test 4: 3 OB snapshots with one spike → spoof detected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ob_imbalance_spoof_detected() -> None:
    """Mixed direction across snapshots → potential_spoof_detected=True."""
    collector, _, _ = _make_collector()
    snapshots = [
        {"bid_vol_top10": 1000, "ask_vol_top10": 500},   # bid heavy
        {"bid_vol_top10": 300, "ask_vol_top10": 1000},    # ask heavy (spike)
        {"bid_vol_top10": 900, "ask_vol_top10": 400},     # bid heavy
    ]

    imb, persistent, spoof = collector._calculate_ob_imbalance(snapshots)

    assert imb is None
    assert persistent is False
    assert spoof is True


# ---------------------------------------------------------------------------
# Test 5: SR level 5% above price, atr_14=1% → proximity ~5.0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sr_proximity_resistance() -> None:
    """S/R level 5% above price with ATR at 1% → proximity ~5.0."""
    collector, _, _ = _make_collector()

    current_price = Decimal("100")
    sr_levels = [Decimal("105")]  # 5% above
    atr_14 = Decimal("1")        # 1% of price

    prox, level, sr_type = collector._calculate_sr_proximity(
        current_price, sr_levels, atr_14,
    )

    assert sr_type == "RESISTANCE"
    assert level == Decimal("105")
    assert abs(prox - 5.0) < 0.01, "Expected proximity ~5.0, got {}".format(prox)


# ---------------------------------------------------------------------------
# Test 6: Code does NOT use any DataFrame method
# ---------------------------------------------------------------------------


def test_no_polars_or_dataframe_usage() -> None:
    """Read collector source as text — verify no pl. or .with_columns calls."""
    source_path = Path(__file__).parent / "collector.py"
    source = source_path.read_text(encoding="utf-8")

    banned_patterns = [
        "pl.",
        ".with_columns",
        "import polars",
        "from polars",
        "import pandas",
        "from pandas",
        "DataFrame",
    ]

    for pattern in banned_patterns:
        assert pattern not in source, (
            "Banned pattern '{}' found in collector.py".format(pattern)
        )


# ---------------------------------------------------------------------------
# Test 7: contributing_to_score is always False, immutable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contributing_to_score_hard_constant() -> None:
    """contributing_to_score defaults False; frozen model prevents mutation."""
    collector, _, _ = _make_collector()
    candles = [
        _make_candle(i, "100", "101", "99", "100", "100")
        for i in range(15)
    ]
    snapshots = [
        {"bid_vol_top10": 500, "ask_vol_top10": 500},
        {"bid_vol_top10": 500, "ask_vol_top10": 500},
        {"bid_vol_top10": 500, "ask_vol_top10": 500},
    ]
    sr_levels = [Decimal("95"), Decimal("105")]
    now = datetime.now(timezone.utc)

    metrics = await collector.collect(
        asset="BTCUSDT",
        ohlcv_data=candles,
        orderbook_snapshots=snapshots,
        sr_levels=sr_levels,
        cycle_timestamp=now,
    )

    assert metrics.contributing_to_score is False

    # Frozen model — attempting to set should raise
    with pytest.raises(Exception):
        metrics.contributing_to_score = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Test 8: store() asyncpg error → ERROR logged, no exception escapes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_asyncpg_error_suppressed() -> None:
    """asyncpg failure in store() → ERROR logged, no exception raised."""
    collector, redis_mock, pool_mock = _make_collector()

    # Make asyncpg raise
    conn_mock = AsyncMock()
    conn_mock.execute = AsyncMock(side_effect=RuntimeError("DB down"))
    pool_mock.acquire.return_value.__aenter__ = AsyncMock(return_value=conn_mock)

    metrics = ShadowMetrics(
        asset="BTCUSDT",
        cycle_timestamp=datetime.now(timezone.utc),
        vwap_deviation=0.05,
        vwap_deviation_direction="ABOVE",
        ob_imbalance=None,
        ob_imbalance_persistent=False,
        ob_potential_spoof_detected=False,
        sr_proximity_atr_normalised=2.5,
        nearest_sr_level=Decimal("105000"),
        nearest_sr_type="RESISTANCE",
        atr_normalised_move=1.2,
        atr_14=Decimal("1500"),
    )

    # Should not raise — errors are logged internally via _log_store_errors
    with patch("shadow.collector.logger") as mock_logger:
        await collector.store(metrics)
        mock_logger.error.assert_called()
        # Verify the postgres failure was specifically logged
        call_args = mock_logger.error.call_args_list
        logged_messages = [c.args[0] if c.args else "" for c in call_args]
        assert any("postgres" in msg for msg in logged_messages), (
            "Expected postgres error log, got: {}".format(logged_messages)
        )

