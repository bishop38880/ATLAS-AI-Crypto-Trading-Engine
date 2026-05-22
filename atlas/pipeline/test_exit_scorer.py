"""Tests for ExitScorer — S2-P4.

All Redis and HYDRA interactions are mocked.  Verifies:
  1. Direction-aware scoring (LONG vs SHORT inversion).
  2. Threshold mapping (EXIT_STRONG, EXIT_PARTIAL, EXIT_WATCH, HOLD).
  3. Concurrent scoring with error isolation.
  4. Signal emission gating (HOLD = no publish).
  5. HYDRA failure graceful degradation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import msgspec
import pytest

from atlas.pipeline.exit_scorer import (
    ExitScorer,
    ExitScoringInputs,
    ExitSignalOutput,
)
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> PolarisSettings:
    return PolarisSettings()


def _make_inputs(
    *,
    funding_curr: str = "0.0001",
    funding_prev: str = "0.0002",
    funding_zscore: float = 1.0,
    whale_zscore: float = 0.5,
    oi_pct: str = "0.01",
    price_pct: str = "0.01",
    breaks: int = 0,
    rsi_div: bool = False,
    liq_data: dict[str, Decimal] | None = None,
) -> ExitScoringInputs:
    """Build ExitScoringInputs with sane defaults."""
    return ExitScoringInputs(
        liquidation_cluster_data=liq_data or {},
        funding_rate_current=Decimal(funding_curr),
        funding_rate_4h_ago=Decimal(funding_prev),
        funding_rate_zscore=funding_zscore,
        whale_outflow_zscore=whale_zscore,
        oi_change_4h_pct=Decimal(oi_pct),
        price_change_4h_pct=Decimal(price_pct),
        timeframe_structure_breaks=breaks,
        rsi_divergence_detected=rsi_div,
    )


def _make_hydra_redis(
    sweep: dict | None = None,
    raises: bool = False,
) -> AsyncMock:
    """Build a mock HYDRA Redis client."""
    mock = AsyncMock()
    if raises:
        mock.get = AsyncMock(side_effect=ConnectionError("hydra down"))
    elif sweep is not None:
        mock.get = AsyncMock(return_value=msgspec.json.encode(sweep))
    else:
        mock.get = AsyncMock(return_value=None)
    return mock


def _make_publisher() -> AsyncMock:
    """Build a mock signal publisher."""
    pub = AsyncMock()
    pub.publish = AsyncMock()
    return pub


def _make_scorer(
    hydra_sweep: dict | None = None,
    hydra_raises: bool = False,
) -> tuple[ExitScorer, AsyncMock]:
    """Build ExitScorer with mocked dependencies."""
    redis_client = AsyncMock()
    hydra = _make_hydra_redis(sweep=hydra_sweep, raises=hydra_raises)
    publisher = _make_publisher()
    scorer = ExitScorer(
        settings=_make_settings(),
        redis_client=redis_client,
        hydra_redis_client=hydra,
        signal_publisher=publisher,
    )
    return scorer, publisher


# ---------------------------------------------------------------------------
# Test 1: All bullish indicators for a LONG → HOLD (low exit score)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bullish_long_hold() -> None:
    """All indicators favour LONG → exit_score low, HOLD."""
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bullish", "active": False},
    )
    inputs = _make_inputs(
        funding_curr="0.0005",   # positive = supports LONG
        funding_prev="0.0004",
        funding_zscore=2.0,      # well above 0 = stable for LONG
        whale_zscore=-0.5,       # inflow = accumulation
        oi_pct="0.05",           # OI expanding
        price_pct="0.03",        # price up
        breaks=0,
        rsi_div=False,
    )

    result = await scorer.score_exit("BTCUSDT", "LONG", 85, inputs)

    assert result.exit_score <= 20
    assert result.exit_recommendation == "HOLD"


# ---------------------------------------------------------------------------
# Test 2: Bearish exit signals for a LONG → EXIT_STRONG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bearish_long_exit_strong() -> None:
    """Funding flipping, whale outflow +2SD, OI diverging → EXIT_STRONG."""
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bearish", "active": True},
    )
    inputs = _make_inputs(
        funding_curr="-0.0003",  # flipped from positive
        funding_prev="0.0002",
        funding_zscore=0.3,
        whale_zscore=2.5,        # > 2 SD outflow
        oi_pct="-0.04",          # OI declining 4%
        price_pct="0.01",        # price still up = divergence
        breaks=2,
        rsi_div=True,
        liq_data={"nearest_opposing_pct": Decimal("0.01")},
    )

    result = await scorer.score_exit("BTCUSDT", "LONG", 85, inputs)

    assert result.exit_score >= 75
    assert result.exit_recommendation == "EXIT_STRONG"


# ---------------------------------------------------------------------------
# Test 3: Same data for SHORT → scores inverted → HOLD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_short_direction_inversion() -> None:
    """Bearish sweep + funding flip for SHORT = supporting → low score."""
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bearish", "active": True},
    )
    # For SHORT: bearish sweep supports position, funding going negative
    # supports SHORT, OI declining while price down = distribution confirms
    inputs = _make_inputs(
        funding_curr="-0.0003",  # negative supports SHORT
        funding_prev="0.0002",   # was positive → flipped, but for SHORT this is good
        funding_zscore=0.3,
        whale_zscore=-0.5,       # inflow = neutral
        oi_pct="0.02",           # OI expanding
        price_pct="-0.02",       # price down (confirms SHORT)
        breaks=0,
        rsi_div=False,
    )

    result = await scorer.score_exit("BTCUSDT", "SHORT", 85, inputs)

    # Bearish sweep does NOT oppose SHORT → liq score = 0 or 8
    # Funding going negative doesn't flip against SHORT → 0
    # Low whale outflow → 0
    # OI expanding with price for shorts = confirming → 0
    assert result.exit_score < 35
    assert result.exit_recommendation == "HOLD"


# ---------------------------------------------------------------------------
# Test 4: Score in 55-74 range → EXIT_PARTIAL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_partial_range() -> None:
    """Mid-range exit signals → EXIT_PARTIAL (score 55-74)."""
    # No opposing sweep → liq = 0 (bullish sweep doesn't oppose LONG)
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bullish", "active": False},
    )
    inputs = _make_inputs(
        funding_curr="-0.0003",  # flipped for LONG → 25
        funding_prev="0.0002",
        funding_zscore=0.3,
        whale_zscore=1.5,        # 1-2 SD → 14
        oi_pct="0.001",          # flat OI + price up → 10
        price_pct="0.01",
        breaks=1,                # single structure break → 8
        rsi_div=False,
    )
    # Expected: liq=0, funding=25, whale=14, oi=10, tech=8 → total=57

    result = await scorer.score_exit("ETHUSDT", "LONG", 75, inputs)

    assert 55 <= result.exit_score <= 74, f"Expected EXIT_PARTIAL range, got {result.exit_score}"
    assert result.exit_recommendation == "EXIT_PARTIAL"


# ---------------------------------------------------------------------------
# Test 5: Gather with 3 assets, 1 raises → other 2 complete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_partial_failure() -> None:
    """One asset raises during scoring → other two complete."""
    scorer, publisher = _make_scorer()
    inputs_ok = _make_inputs()

    # Patch score_exit to raise for one asset
    original = scorer.score_exit

    call_count = 0

    async def _patched(asset: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal call_count
        call_count += 1
        if asset == "FAILCOIN":
            raise RuntimeError("simulated failure")
        return await original(asset, *args, **kwargs)

    scorer.score_exit = _patched  # type: ignore[assignment]

    data_map = {
        "BTCUSDT": inputs_ok,
        "FAILCOIN": inputs_ok,
        "ETHUSDT": inputs_ok,
    }
    results = await scorer.score_all_open_positions(
        ["BTCUSDT", "FAILCOIN", "ETHUSDT"], data_map,
    )

    # FAILCOIN should fail silently, other 2 succeed
    assert len(results) == 2
    assets = {r.asset for r in results}
    assert "BTCUSDT" in assets
    assert "ETHUSDT" in assets
    assert "FAILCOIN" not in assets


# ---------------------------------------------------------------------------
# Test 6: EXIT_STRONG → publisher called once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_strong_publishes() -> None:
    """EXIT_STRONG recommendation → signal_publisher.publish called."""
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bearish", "active": True},
    )
    inputs = _make_inputs(
        funding_curr="-0.0003",
        funding_prev="0.0002",
        funding_zscore=0.3,
        whale_zscore=2.5,
        oi_pct="-0.04",
        price_pct="0.01",
        breaks=2,
        rsi_div=True,
        liq_data={"nearest_opposing_pct": Decimal("0.01")},
    )

    await scorer.score_exit("BTCUSDT", "LONG", 85, inputs)

    publisher.publish.assert_called_once()
    call_args = publisher.publish.call_args
    assert call_args[0][0] == "polaris:signals:BTCUSDT"


# ---------------------------------------------------------------------------
# Test 7: HOLD → publisher NOT called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hold_does_not_publish() -> None:
    """HOLD recommendation → signal_publisher.publish not called."""
    scorer, publisher = _make_scorer(
        hydra_sweep={"direction": "bullish", "active": False},
    )
    inputs = _make_inputs(
        funding_curr="0.0005",
        funding_prev="0.0004",
        funding_zscore=2.0,
        whale_zscore=-0.5,
        oi_pct="0.05",
        price_pct="0.03",
        breaks=0,
        rsi_div=False,
    )

    result = await scorer.score_exit("BTCUSDT", "LONG", 85, inputs)

    assert result.exit_recommendation == "HOLD"
    publisher.publish.assert_not_called()


# ---------------------------------------------------------------------------
# Test 8: HYDRA raises → liquidation_reversal = 8, no exception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hydra_failure_graceful() -> None:
    """HYDRA connection error → liquidation scores 8, rest proceeds."""
    scorer, publisher = _make_scorer(hydra_raises=True)
    inputs = _make_inputs(
        funding_curr="0.0001",
        funding_prev="0.0001",
        funding_zscore=1.0,
        whale_zscore=0.0,
        oi_pct="0.01",
        price_pct="0.01",
        breaks=0,
        rsi_div=False,
    )

    result = await scorer.score_exit("BTCUSDT", "LONG", 85, inputs)

    # Liquidation defaults to 8 on HYDRA failure
    assert result.category_breakdown["liquidation_reversal"] == 8
    # No exception propagated
    assert result.exit_score >= 8
