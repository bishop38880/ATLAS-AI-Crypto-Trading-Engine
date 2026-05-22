"""Deterministic synthetic L3 order book generator for testing.

Produces realistic bid/ask depth profiles with seeded numpy RNG
so every test run is reproducible.  Used by the test suite and
as a development stand-in before live L3 data recording is deployed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from prometheus.backtesting.models import OrderBookSnapshot, PriceLevel


# ── Constants ────────────────────────────────────────────────────────

_DEFAULT_LEVELS: int = 20
_DEFAULT_BASE_SPREAD_BPS: float = 5.0
_TICK_INTERVAL_MS: int = 100


# ── Public API ───────────────────────────────────────────────────────


def generate_book_snapshots(
    symbol: str,
    base_price: float,
    duration_seconds: int,
    seed: int,
    *,
    num_levels: int = _DEFAULT_LEVELS,
    base_spread_bps: float = _DEFAULT_BASE_SPREAD_BPS,
) -> list[OrderBookSnapshot]:
    """Generate a time-series of deterministic order book snapshots.

    Returns one snapshot per 100ms for the requested duration.
    """
    rng = np.random.default_rng(seed)
    num_ticks = (duration_seconds * 1000) // _TICK_INTERVAL_MS
    start = datetime(2025, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    prices = _generate_price_walk(base_price, num_ticks, rng)
    snapshots: list[OrderBookSnapshot] = []

    for i in range(num_ticks):
        ts = start + timedelta(milliseconds=i * _TICK_INTERVAL_MS)
        mid = prices[i]
        regime = "trending" if i < num_ticks // 2 else "ranging"
        snap = _build_snapshot(
            symbol, ts, mid, num_levels, base_spread_bps, rng, regime=regime
        )
        snapshots.append(snap)

    return snapshots


def generate_single_snapshot(
    symbol: str,
    mid_price: float,
    timestamp: datetime,
    seed: int,
    *,
    num_levels: int = _DEFAULT_LEVELS,
    base_spread_bps: float = _DEFAULT_BASE_SPREAD_BPS,
) -> OrderBookSnapshot:
    """Generate a single deterministic order book snapshot."""
    rng = np.random.default_rng(seed)
    return _build_snapshot(
        symbol, timestamp, mid_price, num_levels, base_spread_bps, rng,
    )


# ── Internal Helpers ─────────────────────────────────────────────────


def _generate_price_walk(
    base_price: float,
    num_ticks: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Geometric Brownian motion price walk — vectorised."""
    volatility = 0.0001  # per-tick vol (~1.6% daily at 100ms)
    drift = 0.0
    shocks = rng.normal(drift, volatility, size=num_ticks)
    log_returns = np.cumsum(shocks)
    return base_price * np.exp(log_returns)


def _build_snapshot(
    symbol: str,
    timestamp: datetime,
    mid_price: float,
    num_levels: int,
    base_spread_bps: float,
    rng: np.random.Generator,
    regime: str = "trending",
) -> OrderBookSnapshot:
    """Construct a single snapshot with bid/ask depth."""
    half_spread = mid_price * base_spread_bps / 20000.0
    bids = _generate_levels(
        mid_price - half_spread, num_levels, "bid", rng,
    )
    asks = _generate_levels(
        mid_price + half_spread, num_levels, "ask", rng,
    )
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=timestamp,
        bids=bids,
        asks=asks,
        regime=regime,
    )


def _generate_levels(
    start_price: float,
    num_levels: int,
    side: str,
    rng: np.random.Generator,
) -> list[PriceLevel]:
    """Generate price levels with exponentially decaying size."""
    tick_size = abs(start_price) * 0.0001  # 1 bps tick
    base_size = 0.5 + rng.exponential(1.0)  # base liquidity

    levels: list[PriceLevel] = []
    for i in range(num_levels):
        offset = tick_size * (i + 1)
        if side == "bid":
            price = start_price - offset
        else:
            price = start_price + offset
        # Deeper levels have more size (realistic depth profile).
        depth_mult = 1.0 + rng.exponential(0.5) * (i + 1) / num_levels
        size = base_size * depth_mult
        levels.append(PriceLevel(
            price=Decimal(str(round(price, 8))),
            size=Decimal(str(round(size, 8))),
        ))

    return levels
