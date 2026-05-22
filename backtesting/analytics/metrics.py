"""Performance metric calculations for backtest trade series."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Sequence

import polars as pl
from pydantic import BaseModel

from backtesting.engine.position import ClosedTrade

_BARS_PER_YEAR = 8760
_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")
_KELLY_CAP = Decimal("0.25")
_PROFIT_FACTOR_CAP = Decimal("999999")
_DEFAULT_THRESHOLD_BINS: list[tuple[int, int]] = [
    (120, 139),
    (140, 149),
    (150, 169),
    (170, 179),
    (180, 199),
    (200, 220),
]


class PerformanceMetrics(BaseModel, frozen=True):
    """Complete set of performance metrics for a trade series."""

    total_pnl_usd: Decimal
    total_pnl_pct: Decimal
    annualised_return_pct: Decimal
    cagr_pct: Decimal
    sharpe_ratio: Decimal
    sortino_ratio: Decimal
    calmar_ratio: Decimal
    max_drawdown_pct: Decimal
    max_drawdown_usd: Decimal
    max_drawdown_duration_bars: int
    avg_drawdown_pct: Decimal
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    avg_win_usd: Decimal
    avg_loss_usd: Decimal
    profit_factor: Decimal
    avg_trade_pnl_usd: Decimal
    avg_trade_duration_bars: int
    largest_win_usd: Decimal
    largest_loss_usd: Decimal
    stop_loss_exits: int
    take_profit_exits: int
    signal_decay_exits: int
    max_hold_exits: int
    expectancy_usd: Decimal
    kelly_fraction: Decimal
    risk_reward_ratio: Decimal


class MonthlyReturns(BaseModel, frozen=True):
    """Monthly P&L breakdown for heatmap display."""

    year: int
    month: int
    pnl_usd: Decimal
    pnl_pct: Decimal
    trade_count: int
    win_count: int


class ThresholdAnalysis(BaseModel, frozen=True):
    """Performance breakdown by entry score threshold."""

    min_score: int
    max_score: int
    trade_count: int
    win_rate_pct: Decimal
    avg_pnl_usd: Decimal
    sharpe_ratio: Decimal
    profit_factor: Decimal


def compute_equity_curve(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    sort_by_exit: bool = True,
) -> pl.DataFrame:
    """Build equity curve sorted by exit bar index with drawdown columns."""
    ordered = sorted(trades, key=lambda trade: trade.exit.exit_bar_index) if sort_by_exit else trades
    if not ordered:
        return pl.DataFrame(
            schema={
                "bar_index": pl.Int64,
                "equity_usd": pl.Decimal(precision=20, scale=8),
                "drawdown_pct": pl.Decimal(precision=20, scale=8),
                "drawdown_usd": pl.Decimal(precision=20, scale=8),
            },
        )
    rows: list[dict[str, object]] = []
    cumulative_pnl = _ZERO
    peak_equity = initial_account
    for trade in ordered:
        cumulative_pnl += trade.exit.pnl_usd
        equity = initial_account + cumulative_pnl
        if equity > peak_equity:
            peak_equity = equity
        drawdown_usd = peak_equity - equity
        drawdown_pct = (drawdown_usd / peak_equity * _HUNDRED) if peak_equity > _ZERO else _ZERO
        rows.append(
            {
                "bar_index": trade.exit.exit_bar_index,
                "equity_usd": equity,
                "drawdown_pct": drawdown_pct,
                "drawdown_usd": drawdown_usd,
            },
        )
    return pl.DataFrame(rows)


def compute_sharpe(
    returns: pl.Series,
    bars_per_year: int = _BARS_PER_YEAR,
) -> Decimal:
    """Annualised Sharpe ratio with risk-free rate zero and ddof=1."""
    if returns.len() < 2:
        return _ZERO
    mean_value = _series_mean_decimal(returns)
    std_value = _series_std_decimal(returns, ddof=1)
    if std_value <= _ZERO:
        return Decimal("999999") if mean_value > _ZERO else _ZERO
    scale = Decimal(str(bars_per_year)).sqrt()
    return (mean_value / std_value) * scale


def compute_sortino(returns: pl.Series, bars_per_year: int = _BARS_PER_YEAR) -> Decimal:
    """Annualised Sortino ratio using downside deviation."""
    if returns.len() < 2:
        return _ZERO
    mean_value = _series_mean_decimal(returns)
    downside = [value for value in _series_to_decimals(returns) if value < _ZERO]
    if not downside:
        return Decimal("999999") if mean_value > _ZERO else _ZERO
    downside_std = _std_decimal(downside, ddof=1)
    if downside_std <= _ZERO:
        return Decimal("999999") if mean_value > _ZERO else _ZERO
    scale = Decimal(str(bars_per_year)).sqrt()
    return (mean_value / downside_std) * scale


def compute_max_drawdown(equity_curve: pl.DataFrame) -> tuple[Decimal, int]:
    """Return peak-to-trough drawdown percent and duration in bars."""
    if equity_curve.is_empty():
        return _ZERO, 0
    max_pct = _ZERO
    max_duration = 0
    peak_equity = _ZERO
    peak_bar = 0
    in_drawdown = False
    for row in equity_curve.iter_rows(named=True):
        equity = Decimal(str(row["equity_usd"]))
        bar_index = int(row["bar_index"])
        drawdown_pct = Decimal(str(row["drawdown_pct"]))
        if equity >= peak_equity:
            peak_equity = equity
            peak_bar = bar_index
            in_drawdown = False
        else:
            in_drawdown = True
            duration = bar_index - peak_bar
            max_duration = max(max_duration, duration)
        max_pct = max(max_pct, drawdown_pct)
    if not in_drawdown:
        max_duration = 0
    return max_pct, max_duration


def compute_profit_factor(trades: list[ClosedTrade]) -> Decimal:
    """Gross profit divided by absolute gross loss."""
    gross_profit = sum(
        (trade.exit.pnl_usd for trade in trades if trade.exit.pnl_usd > _ZERO),
        start=_ZERO,
    )
    gross_loss = sum(
        (abs(trade.exit.pnl_usd) for trade in trades if trade.exit.pnl_usd < _ZERO),
        start=_ZERO,
    )
    if gross_loss == _ZERO:
        return Decimal("Infinity") if gross_profit > _ZERO else _ZERO
    return gross_profit / gross_loss


def compute_kelly(win_rate: Decimal, avg_win: Decimal, avg_loss: Decimal) -> Decimal:
    """Full Kelly fraction capped at 25%."""
    if avg_loss <= _ZERO or avg_win <= _ZERO:
        return _ZERO
    win_rate_fraction = win_rate / _HUNDRED
    loss_rate_fraction = _ONE - win_rate_fraction
    reward_ratio = avg_win / avg_loss
    raw_kelly = win_rate_fraction - (loss_rate_fraction / reward_ratio)
    capped = min(max(raw_kelly, _ZERO), _KELLY_CAP)
    return capped


def compute_all_metrics(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    backtest_bars: int,
) -> PerformanceMetrics:
    """Compute all performance metrics from a closed trade list."""
    equity_curve = compute_equity_curve(trades, initial_account)
    trade_stats = _compute_trade_statistics(trades, initial_account)
    return_stats = _compute_return_metrics(
        trades,
        initial_account,
        backtest_bars,
        equity_curve,
        trade_stats,
    )
    return PerformanceMetrics(**trade_stats, **return_stats)


def compute_monthly_returns(
    trades: list[ClosedTrade],
    initial_account: Decimal,
) -> list[MonthlyReturns]:
    """Group closed trades by exit calendar month."""
    if not trades:
        return []
    monthly_groups = _group_trades_by_exit_month(trades)
    results: list[MonthlyReturns] = []
    running_account = initial_account
    for (year, month) in sorted(monthly_groups.keys()):
        month_trades = monthly_groups[(year, month)]
        month_pnl = sum((trade.exit.pnl_usd for trade in month_trades), start=_ZERO)
        win_count = sum(1 for trade in month_trades if trade.exit.pnl_usd > _ZERO)
        pnl_pct = (month_pnl / running_account * _HUNDRED) if running_account > _ZERO else _ZERO
        results.append(
            MonthlyReturns(
                year=year,
                month=month,
                pnl_usd=month_pnl,
                pnl_pct=pnl_pct,
                trade_count=len(month_trades),
                win_count=win_count,
            ),
        )
        running_account += month_pnl
    return results


def compute_threshold_analysis(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    bins: list[tuple[int, int]] | None = None,
) -> list[ThresholdAnalysis]:
    """Break down performance by entry score threshold bins."""
    score_bins = bins or _DEFAULT_THRESHOLD_BINS
    results: list[ThresholdAnalysis] = []
    for min_score, max_score in score_bins:
        bucket = [
            trade
            for trade in trades
            if min_score <= trade.entry.score_at_entry <= max_score
        ]
        results.append(_threshold_metrics_for_bucket(bucket, min_score, max_score, initial_account))
    return results


def _compute_trade_statistics(
    trades: list[ClosedTrade],
    initial_account: Decimal,
) -> dict[str, object]:
    if not trades:
        return _empty_trade_statistics()
    wins = [trade for trade in trades if trade.exit.pnl_usd > _ZERO]
    losses = [trade for trade in trades if trade.exit.pnl_usd < _ZERO]
    win_rate = Decimal(len(wins)) / Decimal(len(trades)) * _HUNDRED
    avg_win = _average_decimal([trade.exit.pnl_usd for trade in wins])
    avg_loss = _average_decimal([abs(trade.exit.pnl_usd) for trade in losses])
    avg_pnl = _average_decimal([trade.exit.pnl_usd for trade in trades])
    loss_rate = _HUNDRED - win_rate
    expectancy = (avg_win * win_rate / _HUNDRED) - (avg_loss * loss_rate / _HUNDRED)
    rr_ratio = (avg_win / avg_loss) if avg_loss > _ZERO else _ZERO
    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate_pct": win_rate,
        "avg_win_usd": avg_win,
        "avg_loss_usd": avg_loss,
        "profit_factor": _finite_profit_factor(compute_profit_factor(trades)),
        "avg_trade_pnl_usd": avg_pnl,
        "avg_trade_duration_bars": int(
            sum(trade.exit.duration_bars for trade in trades) / len(trades),
        ),
        "largest_win_usd": max((trade.exit.pnl_usd for trade in wins), default=_ZERO),
        "largest_loss_usd": min((trade.exit.pnl_usd for trade in losses), default=_ZERO),
        "stop_loss_exits": sum(1 for trade in trades if trade.exit.exit_reason == "STOP_LOSS"),
        "take_profit_exits": sum(1 for trade in trades if trade.exit.exit_reason == "TAKE_PROFIT"),
        "signal_decay_exits": sum(1 for trade in trades if trade.exit.exit_reason == "SIGNAL_DECAY"),
        "max_hold_exits": sum(1 for trade in trades if trade.exit.exit_reason == "MAX_HOLD_BARS"),
        "expectancy_usd": expectancy,
        "kelly_fraction": compute_kelly(win_rate, avg_win, avg_loss),
        "risk_reward_ratio": rr_ratio,
    }


def _compute_return_metrics(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    backtest_bars: int,
    equity_curve: pl.DataFrame,
    trade_stats: dict[str, object],
) -> dict[str, Decimal | int]:
    total_pnl = sum((trade.exit.pnl_usd for trade in trades), start=_ZERO)
    total_pnl_pct = (total_pnl / initial_account * _HUNDRED) if initial_account > _ZERO else _ZERO
    returns = _trade_return_series(trades)
    sharpe = compute_sharpe(returns, _annualisation_factor(trades, backtest_bars))
    sortino = compute_sortino(returns, _annualisation_factor(trades, backtest_bars))
    max_dd_pct, max_dd_duration = compute_max_drawdown(equity_curve)
    max_dd_usd = _max_drawdown_usd(equity_curve)
    avg_dd_pct = _average_drawdown_pct(equity_curve)
    cagr = _compute_cagr(initial_account, total_pnl, backtest_bars)
    annualised = _compute_annualised_return(total_pnl_pct, backtest_bars)
    calmar = (cagr / max_dd_pct) if max_dd_pct > _ZERO else _ZERO
    return {
        "total_pnl_usd": total_pnl,
        "total_pnl_pct": total_pnl_pct,
        "annualised_return_pct": annualised,
        "cagr_pct": cagr,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "calmar_ratio": calmar,
        "max_drawdown_pct": max_dd_pct,
        "max_drawdown_usd": max_dd_usd,
        "max_drawdown_duration_bars": max_dd_duration,
        "avg_drawdown_pct": avg_dd_pct,
    }


def _threshold_metrics_for_bucket(
    trades: list[ClosedTrade],
    min_score: int,
    max_score: int,
    initial_account: Decimal,
) -> ThresholdAnalysis:
    if not trades:
        return ThresholdAnalysis(
            min_score=min_score,
            max_score=max_score,
            trade_count=0,
            win_rate_pct=_ZERO,
            avg_pnl_usd=_ZERO,
            sharpe_ratio=_ZERO,
            profit_factor=_ZERO,
        )
    wins = sum(1 for trade in trades if trade.exit.pnl_usd > _ZERO)
    win_rate = Decimal(wins) / Decimal(len(trades)) * _HUNDRED
    avg_pnl = _average_decimal([trade.exit.pnl_usd for trade in trades])
    returns = _trade_return_series(trades)
    sharpe = compute_sharpe(returns, len(trades))
    return ThresholdAnalysis(
        min_score=min_score,
        max_score=max_score,
        trade_count=len(trades),
        win_rate_pct=win_rate,
        avg_pnl_usd=avg_pnl,
        sharpe_ratio=sharpe,
        profit_factor=_finite_profit_factor(compute_profit_factor(trades)),
    )


def _empty_trade_statistics() -> dict[str, object]:
    return {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate_pct": _ZERO,
        "avg_win_usd": _ZERO,
        "avg_loss_usd": _ZERO,
        "profit_factor": _ZERO,
        "avg_trade_pnl_usd": _ZERO,
        "avg_trade_duration_bars": 0,
        "largest_win_usd": _ZERO,
        "largest_loss_usd": _ZERO,
        "stop_loss_exits": 0,
        "take_profit_exits": 0,
        "signal_decay_exits": 0,
        "max_hold_exits": 0,
        "expectancy_usd": _ZERO,
        "kelly_fraction": _ZERO,
        "risk_reward_ratio": _ZERO,
    }


def _trade_return_series(trades: list[ClosedTrade]) -> pl.Series:
    values = [trade.exit.pnl_pct / _HUNDRED for trade in trades]
    return pl.Series("returns", values, dtype=pl.Decimal(precision=20, scale=8))


def _annualisation_factor(trades: list[ClosedTrade], backtest_bars: int) -> int:
    if backtest_bars <= 0 or not trades:
        return _BARS_PER_YEAR
    trades_per_year = max(int(len(trades) / backtest_bars * _BARS_PER_YEAR), 1)
    return trades_per_year


def _compute_cagr(initial_account: Decimal, total_pnl: Decimal, backtest_bars: int) -> Decimal:
    if initial_account <= _ZERO or backtest_bars <= 0:
        return _ZERO
    final_value = initial_account + total_pnl
    if final_value <= _ZERO:
        return Decimal("-100")
    growth = final_value / initial_account
    years = Decimal(backtest_bars) / Decimal(_BARS_PER_YEAR)
    if years <= _ZERO:
        return _ZERO
    exponent = _ONE / years
    cagr = (growth ** exponent - _ONE) * _HUNDRED
    return cagr


def _compute_annualised_return(total_pnl_pct: Decimal, backtest_bars: int) -> Decimal:
    if backtest_bars <= 0:
        return _ZERO
    years = Decimal(backtest_bars) / Decimal(_BARS_PER_YEAR)
    if years <= _ZERO:
        return _ZERO
    return total_pnl_pct / years


def _max_drawdown_usd(equity_curve: pl.DataFrame) -> Decimal:
    if equity_curve.is_empty():
        return _ZERO
    return max(Decimal(str(value)) for value in equity_curve["drawdown_usd"].to_list())


def _average_drawdown_pct(equity_curve: pl.DataFrame) -> Decimal:
    if equity_curve.is_empty():
        return _ZERO
    values = [Decimal(str(value)) for value in equity_curve["drawdown_pct"].to_list()]
    return _average_decimal(values)


def _group_trades_by_exit_month(
    trades: list[ClosedTrade],
) -> dict[tuple[int, int], list[ClosedTrade]]:
    groups: dict[tuple[int, int], list[ClosedTrade]] = {}
    for trade in trades:
        timestamp = datetime.fromisoformat(trade.exit.exit_timestamp_utc.replace("Z", "+00:00"))
        key = (timestamp.year, timestamp.month)
        groups.setdefault(key, []).append(trade)
    return groups


def _series_to_decimals(series: pl.Series) -> list[Decimal]:
    return [Decimal(str(value)) for value in series.to_list()]


def _series_mean_decimal(series: pl.Series) -> Decimal:
    values = _series_to_decimals(series)
    return _average_decimal(values)


def _series_std_decimal(series: pl.Series, ddof: int) -> Decimal:
    return _std_decimal(_series_to_decimals(series), ddof=ddof)


def _finite_profit_factor(value: Decimal) -> Decimal:
    if value.is_infinite():
        return _PROFIT_FACTOR_CAP
    return value


def _average_decimal(values: Sequence[Decimal]) -> Decimal:
    if not values:
        return _ZERO
    return sum(values, start=_ZERO) / Decimal(len(values))


def _std_decimal(values: Sequence[Decimal], ddof: int) -> Decimal:
    if len(values) <= ddof:
        return _ZERO
    mean_value = _average_decimal(values)
    variance = sum((value - mean_value) ** 2 for value in values) / Decimal(len(values) - ddof)
    return variance.sqrt()
