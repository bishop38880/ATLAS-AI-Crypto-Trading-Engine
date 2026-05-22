"""Per-dimension score attribution for closed trades."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from backtesting.engine.position import ClosedTrade

_DIMENSIONS: tuple[str, ...] = ("derivatives", "technical", "market_context")
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class DimensionAttribution(BaseModel, frozen=True):
    """Contribution of a scoring dimension to trade entries."""

    dimension: str = Field(description="derivatives, technical, or market_context")
    avg_contribution_pct: Decimal
    avg_contribution_pts: Decimal
    win_rate_when_dominant: Decimal
    trade_count_dominant: int


def compute_dimension_attribution(
    trades: list[ClosedTrade],
) -> list[DimensionAttribution]:
    """Measure which scoring dimensions drove profitable entries."""
    if not trades:
        return [
            DimensionAttribution(
                dimension=dimension,
                avg_contribution_pct=_ZERO,
                avg_contribution_pts=_ZERO,
                win_rate_when_dominant=_ZERO,
                trade_count_dominant=0,
            )
            for dimension in _DIMENSIONS
        ]
    return [_attribution_for_dimension(trades, dimension) for dimension in _DIMENSIONS]


def _attribution_for_dimension(
    trades: list[ClosedTrade],
    dimension: str,
) -> DimensionAttribution:
    points_list: list[Decimal] = []
    pct_list: list[Decimal] = []
    dominant_trades: list[ClosedTrade] = []
    for trade in trades:
        breakdown = _dimension_points(trade)
        total_score = sum(breakdown.values(), start=0)
        if total_score <= 0:
            continue
        dimension_points = breakdown[dimension]
        points_list.append(Decimal(dimension_points))
        pct_list.append(Decimal(dimension_points) / Decimal(total_score) * _HUNDRED)
        if _is_dominant_dimension(breakdown, dimension):
            dominant_trades.append(trade)
    dominant_wins = sum(1 for trade in dominant_trades if trade.exit.pnl_usd > _ZERO)
    dominant_win_rate = (
        Decimal(dominant_wins) / Decimal(len(dominant_trades)) * _HUNDRED
        if dominant_trades
        else _ZERO
    )
    return DimensionAttribution(
        dimension=dimension,
        avg_contribution_pct=_average(pct_list),
        avg_contribution_pts=_average(points_list),
        win_rate_when_dominant=dominant_win_rate,
        trade_count_dominant=len(dominant_trades),
    )


def _dimension_points(trade: ClosedTrade) -> dict[str, int]:
    entry = trade.entry
    return {
        "derivatives": entry.derivatives_score,
        "technical": entry.technical_score,
        "market_context": entry.market_context_score,
    }


def _is_dominant_dimension(breakdown: dict[str, int], dimension: str) -> bool:
    total = sum(breakdown.values(), start=0)
    if total <= 0:
        return False
    return breakdown[dimension] > total / 2


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return _ZERO
    return sum(values, start=_ZERO) / Decimal(len(values))
