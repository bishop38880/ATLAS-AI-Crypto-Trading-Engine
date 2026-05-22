"""Regime-stratified performance breakdown."""

from __future__ import annotations

from decimal import Decimal

import polars as pl
from pydantic import BaseModel, Field

from backtesting.engine.position import ClosedTrade

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class RegimePerformance(BaseModel, frozen=True):
    """Performance metrics for a specific market regime."""

    regime: str = Field(description="BULL_TREND, BEAR_TREND, VOLATILE, or RANGING")
    trade_count: int
    win_rate_pct: Decimal
    avg_pnl_usd: Decimal
    profit_factor: Decimal
    best_trade_usd: Decimal
    worst_trade_usd: Decimal


def classify_bar_regime(
    close_series: list[Decimal],
    adx_series: list[Decimal],
    atr_ratio: Decimal,
) -> str:
    """Classify a bar into one of four market regimes."""
    if atr_ratio > Decimal("1.5"):
        return "VOLATILE"
    adx_value = adx_series[-1] if adx_series else Decimal("0")
    close_value = close_series[-1] if close_series else Decimal("0")
    ma_50 = _simple_moving_average(close_series, 50)
    if adx_value < Decimal("20") and atr_ratio < Decimal("1.0"):
        return "RANGING"
    if adx_value > Decimal("25"):
        if close_value > ma_50:
            return "BULL_TREND"
        if close_value < ma_50:
            return "BEAR_TREND"
    return "RANGING"


def compute_regime_breakdown(
    trades: list[ClosedTrade],
    ohlcv: pl.DataFrame,
) -> list[RegimePerformance]:
    """Classify entry-bar regime and aggregate trade performance."""
    regime_labels = _build_regime_labels(ohlcv)
    grouped: dict[str, list[ClosedTrade]] = {}
    for trade in trades:
        entry_index = trade.entry.entry_bar_index
        if entry_index < 0 or entry_index >= len(regime_labels):
            continue
        regime = regime_labels[entry_index]
        grouped.setdefault(regime, []).append(trade)
    ordered_regimes = ["BULL_TREND", "BEAR_TREND", "VOLATILE", "RANGING"]
    return [_metrics_for_regime(regime, grouped.get(regime, [])) for regime in ordered_regimes]


def _build_regime_labels(ohlcv: pl.DataFrame) -> list[str]:
    if ohlcv.is_empty():
        return []
    closes = [Decimal(str(value)) for value in ohlcv["close"].to_list()]
    adx_values = _compute_adx_series(ohlcv)
    atr_ratios = _compute_atr_ratio_series(ohlcv)
    labels: list[str] = []
    for index in range(len(closes)):
        close_window = closes[: index + 1]
        adx_window = adx_values[: index + 1]
        labels.append(classify_bar_regime(close_window, adx_window, atr_ratios[index]))
    return labels


def _metrics_for_regime(regime: str, trades: list[ClosedTrade]) -> RegimePerformance:
    if not trades:
        return RegimePerformance(
            regime=regime,
            trade_count=0,
            win_rate_pct=_ZERO,
            avg_pnl_usd=_ZERO,
            profit_factor=_ZERO,
            best_trade_usd=_ZERO,
            worst_trade_usd=_ZERO,
        )
    wins = sum(1 for trade in trades if trade.exit.pnl_usd > _ZERO)
    pnls = [trade.exit.pnl_usd for trade in trades]
    gross_profit = sum((pnl for pnl in pnls if pnl > _ZERO), start=_ZERO)
    gross_loss = sum((abs(pnl) for pnl in pnls if pnl < _ZERO), start=_ZERO)
    if gross_loss > _ZERO:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > _ZERO:
        profit_factor = Decimal("999999")
    else:
        profit_factor = _ZERO
    return RegimePerformance(
        regime=regime,
        trade_count=len(trades),
        win_rate_pct=Decimal(wins) / Decimal(len(trades)) * _HUNDRED,
        avg_pnl_usd=sum(pnls, start=_ZERO) / Decimal(len(trades)),
        profit_factor=profit_factor,
        best_trade_usd=max(pnls),
        worst_trade_usd=min(pnls),
    )


def _simple_moving_average(values: list[Decimal], period: int) -> Decimal:
    if not values:
        return _ZERO
    window = values[-period:] if len(values) >= period else values
    return sum(window, start=_ZERO) / Decimal(len(window))


def _compute_atr_ratio_series(ohlcv: pl.DataFrame) -> list[Decimal]:
    highs = [float(value) for value in ohlcv["high"].to_list()]
    lows = [float(value) for value in ohlcv["low"].to_list()]
    closes = [float(value) for value in ohlcv["close"].to_list()]
    true_ranges: list[float] = []
    for index in range(len(closes)):
        if index == 0:
            true_ranges.append(highs[index] - lows[index])
            continue
        true_ranges.append(
            max(
                highs[index] - lows[index],
                abs(highs[index] - closes[index - 1]),
                abs(lows[index] - closes[index - 1]),
            ),
        )
    ratios: list[Decimal] = []
    for index in range(len(true_ranges)):
        window = true_ranges[max(0, index - 20) : index]
        mean_atr = sum(window) / len(window) if window else true_ranges[index]
        current = true_ranges[index]
        ratio = current / mean_atr if mean_atr > 0 else 1.0
        ratios.append(Decimal(str(ratio)))
    return ratios


def _compute_adx_series(ohlcv: pl.DataFrame) -> list[Decimal]:
    highs = [float(value) for value in ohlcv["high"].to_list()]
    lows = [float(value) for value in ohlcv["low"].to_list()]
    closes = [float(value) for value in ohlcv["close"].to_list()]
    period = 14
    adx_values: list[Decimal] = []
    for index in range(len(closes)):
        if index < period + 1:
            adx_values.append(Decimal("0"))
            continue
        adx_values.append(Decimal(str(_adx_at_index(highs, lows, closes, index, period))))
    return adx_values


def _adx_at_index(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    index: int,
    period: int,
) -> float:
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for bar_index in range(1, index + 1):
        true_ranges.append(
            max(
                highs[bar_index] - lows[bar_index],
                abs(highs[bar_index] - closes[bar_index - 1]),
                abs(lows[bar_index] - closes[bar_index - 1]),
            ),
        )
        up_move = highs[bar_index] - highs[bar_index - 1]
        down_move = lows[bar_index - 1] - lows[bar_index]
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    atr = sum(true_ranges[:period]) / period
    plus_di = 100 * sum(plus_dm[:period]) / period / atr if atr else 0.0
    minus_di = 100 * sum(minus_dm[:period]) / period / atr if atr else 0.0
    for bar_index in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[bar_index]) / period
        plus_di = (plus_di * (period - 1) + 100 * plus_dm[bar_index] / atr) / period if atr else 0.0
        minus_di = (minus_di * (period - 1) + 100 * minus_dm[bar_index] / atr) / period if atr else 0.0
    if plus_di + minus_di == 0:
        return 0.0
    return abs(plus_di - minus_di) / (plus_di + minus_di) * 100
