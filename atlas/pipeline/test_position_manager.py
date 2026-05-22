"""Tests for PositionSizingRecommender — S2-P3.

All Redis interactions are mocked.  Verifies:
  1. Decimal arithmetic exactness (no float drift).
  2. Read-only Redis contract (no set/setex calls).
  3. All rejection paths return valid recommendation objects.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from atlas.pipeline.position_manager import (
    BASE_SIZE_MODERATE,
    BASE_SIZE_STRONG,
    CASCADE_PROBE_MULT,
    PositionManager,
    PositionSizeRecommendation,
    PositionSizingRecommender,
    PositionState,
    ScaleInRecommendation,
)
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> PolarisSettings:
    """Create a minimal PolarisSettings for testing."""
    return PolarisSettings()


def _make_signal(score: int) -> MagicMock:
    """Create a mock SignalOutput with the given score."""
    sig = MagicMock()
    sig.score = score
    return sig


def _make_redis(
    portfolio_value: str | None = "10000",
    open_positions: list[str] | None = None,
    position_state: dict | None = None,
    scale_in_count: int | None = None,
) -> AsyncMock:
    """Build a mock Redis client with pre-configured return values."""
    mock = AsyncMock()

    async def _get(key: str) -> bytes | None:
        if key == "portfolio:value" and portfolio_value is not None:
            return portfolio_value.encode()
        if key == "positions:open" and open_positions is not None:
            return msgspec.json.encode(open_positions)
        if key.startswith("position:") and key.endswith(":state"):
            if position_state is not None:
                return msgspec.json.encode(position_state)
            return None
        if key.startswith("position:") and key.endswith(":scale_in_count"):
            if scale_in_count is not None:
                return str(scale_in_count).encode()
            return None
        return None

    mock.get = AsyncMock(side_effect=_get)
    return mock


# ---------------------------------------------------------------------------
# Test 1: STRONG signal entry sizing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_strong_signal_entry_sizing() -> None:
    """STRONG signal (score=85), portfolio=$10000 → 5%, $500."""
    redis = _make_redis(portfolio_value="10000", open_positions=[])
    rec = PositionSizingRecommender(_make_settings(), redis)
    signal = _make_signal(score=85)

    result = await rec.calculate_entry_size(signal, "BTCUSDT")

    assert result.status == "APPROVED"
    assert result.recommended_pct_portfolio == Decimal("0.050")
    assert result.recommended_usd == Decimal("500.00") or result.recommended_usd == Decimal("500.000")
    # Verify exact Decimal — no float drift
    assert isinstance(result.recommended_usd, Decimal)
    assert result.recommended_usd == Decimal("0.050") * Decimal("10000")
    assert result.signal_score == 85
    assert result.is_cascade_probe is False


# ---------------------------------------------------------------------------
# Test 2: CASCADE probe sizing (MODERATE × 0.25)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cascade_probe_sizing() -> None:
    """MODERATE signal (score=70), cascade stage1 → 3.5% × 0.25 = 0.875%."""
    redis = _make_redis(portfolio_value="10000", open_positions=[])
    rec = PositionSizingRecommender(_make_settings(), redis)
    signal = _make_signal(score=70)

    result = await rec.calculate_entry_size(
        signal, "ETHUSDT", is_cascade_stage1=True,
    )

    expected_pct = BASE_SIZE_MODERATE * CASCADE_PROBE_MULT
    assert result.status == "APPROVED"
    assert result.recommended_pct_portfolio == expected_pct
    assert result.recommended_pct_portfolio == Decimal("0.00875")
    assert result.is_cascade_probe is True
    assert result.recommended_usd == expected_pct * Decimal("10000")


# ---------------------------------------------------------------------------
# Test 3: SLOTS_FULL rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slots_full_rejection() -> None:
    """6 positions open → REJECTED with SLOTS_FULL."""
    open_6 = ["BTC", "ETH", "SOL", "DOGE", "LINK", "AVAX"]
    redis = _make_redis(portfolio_value="10000", open_positions=open_6)
    rec = PositionSizingRecommender(_make_settings(), redis)
    signal = _make_signal(score=90)

    result = await rec.calculate_entry_size(signal, "SUIUSDT")

    assert result.status == "REJECTED"
    assert result.rejection_reason == "SLOTS_FULL"
    assert result.recommended_usd == Decimal("0")


# ---------------------------------------------------------------------------
# Test 4: Scale-in losing position rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scale_in_losing_position_rejected() -> None:
    """pnl = -0.2% → REJECTED, POSITION_LOSING."""
    state = {
        "asset": "BTCUSDT",
        "position_size_usd": "1000",
        "entry_price": "50000",
        "side": "buy",
        "opened_at": "2026-04-26T00:00:00Z",
    }
    redis = _make_redis(position_state=state, scale_in_count=0)
    rec = PositionSizingRecommender(_make_settings(), redis)

    result = await rec.calculate_scale_in("BTCUSDT", Decimal("-0.002"))

    assert result.status == "REJECTED"
    assert result.rejection_reason == "POSITION_LOSING"


# ---------------------------------------------------------------------------
# Test 5: Scale-in above threshold → APPROVED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scale_in_above_threshold_approved() -> None:
    """pnl = +0.8%, count=0 → APPROVED, count_after=1."""
    state = {
        "asset": "BTCUSDT",
        "position_size_usd": "1000",
        "entry_price": "50000",
        "side": "buy",
        "opened_at": "2026-04-26T00:00:00Z",
    }
    redis = _make_redis(position_state=state, scale_in_count=0)
    rec = PositionSizingRecommender(_make_settings(), redis)

    result = await rec.calculate_scale_in("BTCUSDT", Decimal("0.008"))

    assert result.status == "APPROVED"
    assert result.scale_in_count_after == 1
    assert result.recommended_add_usd == Decimal("500")
    assert result.current_position_usd == Decimal("1000")


# ---------------------------------------------------------------------------
# Test 6: MAX_ADDS_REACHED rejection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scale_in_max_adds_reached() -> None:
    """count=2, pnl=+1% → REJECTED, MAX_ADDS_REACHED."""
    state = {
        "asset": "ETHUSDT",
        "position_size_usd": "2000",
        "entry_price": "3000",
        "side": "buy",
        "opened_at": "2026-04-26T00:00:00Z",
    }
    redis = _make_redis(position_state=state, scale_in_count=2)
    rec = PositionSizingRecommender(_make_settings(), redis)

    result = await rec.calculate_scale_in("ETHUSDT", Decimal("0.010"))

    assert result.status == "REJECTED"
    assert result.rejection_reason == "MAX_ADDS_REACHED"


# ---------------------------------------------------------------------------
# Test 7: portfolio_value=0 → REJECTED, PORTFOLIO_UNKNOWN
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zero_portfolio_rejected() -> None:
    """portfolio_value = '0' → REJECTED, PORTFOLIO_UNKNOWN."""
    redis = _make_redis(portfolio_value="0", open_positions=[])
    rec = PositionSizingRecommender(_make_settings(), redis)
    signal = _make_signal(score=85)

    result = await rec.calculate_entry_size(signal, "BTCUSDT")

    assert result.status == "REJECTED"
    assert result.rejection_reason == "PORTFOLIO_UNKNOWN"


# ---------------------------------------------------------------------------
# Test 8: NO write calls to Redis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_redis_writes() -> None:
    """Verify ATLAS never writes portfolio:value or positions:open."""
    redis = _make_redis(portfolio_value="10000", open_positions=[])
    rec = PositionSizingRecommender(_make_settings(), redis)
    signal = _make_signal(score=85)

    await rec.calculate_entry_size(signal, "BTCUSDT")

    # Assert set/setex/hset/mset were never called
    redis.set.assert_not_called()
    redis.setex.assert_not_called()
    redis.hset.assert_not_called()
    redis.mset.assert_not_called()
    # Assert delete was never called
    redis.delete.assert_not_called()


# ---------------------------------------------------------------------------
# Alias sanity check
# ---------------------------------------------------------------------------


def test_position_manager_alias() -> None:
    """PositionManager alias resolves to PositionSizingRecommender."""
    assert PositionManager is PositionSizingRecommender
