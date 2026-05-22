"""Pure analytics helpers for hourly market monitoring."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from atlas.monitoring.models import (
    AssetHourlyAnalytics,
    BasketHourlyAnalytics,
    NormalizedHourlyQuote,
)


def calculate_hourly_return_pct(current: Decimal, previous: Decimal | None) -> float | None:
    """Percent change vs prior hourly close."""
    if previous is None or previous <= 0 or current <= 0:
        return None
    return float((current - previous) / previous * Decimal("100"))


def calculate_rolling_volatility_pct(returns_pct: list[float]) -> float | None:
    """Sample standard deviation of hourly returns (percent units)."""
    if len(returns_pct) < 2:
        return None
    mean = sum(returns_pct) / len(returns_pct)
    variance = sum((value - mean) ** 2 for value in returns_pct) / (len(returns_pct) - 1)
    return math.sqrt(variance)


def calculate_momentum_pct(prices: list[Decimal], lookback: int = 6) -> float | None:
    """Rate-of-change vs ``lookback`` hours ago."""
    if len(prices) < lookback + 1:
        return None
    start = prices[-(lookback + 1)]
    end = prices[-1]
    if start <= 0 or end <= 0:
        return None
    return float((end - start) / start * Decimal("100"))


def calculate_volume_z_score(current: Decimal, history: list[Decimal]) -> float | None:
    """Z-score of 24h volume vs rolling history."""
    if not history or current <= 0:
        return None
    floats = [float(value) for value in history if value > 0]
    if len(floats) < 2:
        return None
    mean = sum(floats) / len(floats)
    variance = sum((value - mean) ** 2 for value in floats) / (len(floats) - 1)
    if variance <= 0:
        return None
    return (float(current) - mean) / math.sqrt(variance)


def calculate_relative_strength(asset_return_pct: float | None, basket_return_pct: float | None) -> float | None:
    """Asset return minus equal-weight basket return."""
    if asset_return_pct is None or basket_return_pct is None:
        return None
    return asset_return_pct - basket_return_pct


def calculate_rank_changes(
    ranks_now: dict[str, int],
    ranks_prev: dict[str, int],
) -> dict[str, int]:
    """Positive rank_change means improved rank (lower number = better)."""
    changes: dict[str, int] = {}
    for asset, rank_now in ranks_now.items():
        rank_prev = ranks_prev.get(asset)
        if rank_prev is None:
            continue
        changes[asset] = rank_prev - rank_now
    return changes


def calculate_basket_breadth(returns_pct: list[float | None]) -> float:
    """Share of assets with strictly positive hourly return."""
    valid = [value for value in returns_pct if value is not None]
    if not valid:
        return 0.0
    positive = sum(1 for value in valid if value > 0)
    return positive / len(valid) * 100.0


def build_asset_analytics(
    *,
    quote: NormalizedHourlyQuote,
    previous_price: Decimal | None,
    return_history: list[float],
    price_history: list[Decimal],
    volume_history: list[Decimal],
    market_cap_rank: int | None,
    rank_change: int | None,
    basket_return_pct: float | None,
) -> AssetHourlyAnalytics:
    """Assemble per-asset analytics for one cycle."""
    hourly_return = calculate_hourly_return_pct(quote.price_usd, previous_price)
    return AssetHourlyAnalytics(
        asset_base=quote.asset_base,
        sampled_at=quote.sampled_at,
        hourly_return_pct=hourly_return,
        rolling_volatility_pct=calculate_rolling_volatility_pct(return_history),
        momentum_pct=calculate_momentum_pct(price_history),
        volume_z_score=calculate_volume_z_score(
            quote.volume_24h_usd or Decimal("0"),
            volume_history,
        ),
        market_cap_rank=market_cap_rank,
        rank_change=rank_change,
        relative_strength=calculate_relative_strength(hourly_return, basket_return_pct),
    )


def build_basket_analytics(
    *,
    sampled_at: datetime,
    asset_returns: list[float | None],
    benchmark_asset: str = "BTC",
) -> BasketHourlyAnalytics:
    """Cross-sectional basket summary."""
    valid_returns = [value for value in asset_returns if value is not None]
    median_return: float | None = None
    if valid_returns:
        sorted_returns = sorted(valid_returns)
        mid = len(sorted_returns) // 2
        if len(sorted_returns) % 2 == 1:
            median_return = sorted_returns[mid]
        else:
            median_return = (sorted_returns[mid - 1] + sorted_returns[mid]) / 2.0
    return BasketHourlyAnalytics(
        sampled_at=sampled_at,
        breadth_positive_pct=calculate_basket_breadth(asset_returns),
        median_hourly_return_pct=median_return,
        benchmark_asset=benchmark_asset,
    )
