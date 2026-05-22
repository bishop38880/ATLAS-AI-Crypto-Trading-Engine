"""Backtest analytics result assembly and verdict generation."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import polars as pl

from backtesting.analytics.attribution import DimensionAttribution, compute_dimension_attribution
from backtesting.analytics.metrics import (
    MonthlyReturns,
    PerformanceMetrics,
    ThresholdAnalysis,
    compute_all_metrics,
    compute_equity_curve,
    compute_monthly_returns,
    compute_threshold_analysis,
)
from backtesting.analytics.monte_carlo import MonteCarloConfig, MonteCarloResult, run_monte_carlo
from backtesting.analytics.regime import RegimePerformance, compute_regime_breakdown
from backtesting.engine.config import BacktestConfig
from pydantic import BaseModel

from backtesting.engine.position import ClosedTrade

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


class BacktestResult(BaseModel, frozen=True):
    """Complete analytics output for a backtest run."""

    config: BacktestConfig
    completed_at: str
    duration_seconds: Decimal
    trades: list[ClosedTrade]
    equity_curve: list[dict[str, object]]
    metrics: PerformanceMetrics
    monthly_returns: list[MonthlyReturns]
    threshold_analysis: list[ThresholdAnalysis]
    regime_performance: list[RegimePerformance]
    dimension_attribution: list[DimensionAttribution]
    monte_carlo: MonteCarloResult | None
    per_asset_metrics: dict[str, PerformanceMetrics]
    verdict: str
    verdict_detail: list[str]


def build_backtest_result(
    trades: list[ClosedTrade],
    config: BacktestConfig,
    backtest_bars: int,
    ohlcv: pl.DataFrame | None = None,
    mc_config: MonteCarloConfig | None = None,
    duration_seconds: Decimal = Decimal("0"),
) -> BacktestResult:
    """Assemble analytics outputs from closed trades."""
    initial_account = config.risk.account_size_usd
    metrics = compute_all_metrics(trades, initial_account, backtest_bars)
    equity_df = compute_equity_curve(trades, initial_account)
    verdict, verdict_detail = generate_verdict(metrics, config)
    regime_rows = compute_regime_breakdown(trades, ohlcv) if ohlcv is not None else []
    monte_carlo = run_monte_carlo(trades, initial_account, mc_config) if mc_config else None
    return BacktestResult(
        config=config,
        completed_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=duration_seconds,
        trades=trades,
        equity_curve=_serialise_equity_curve(equity_df),
        metrics=metrics,
        monthly_returns=compute_monthly_returns(trades, initial_account),
        threshold_analysis=compute_threshold_analysis(trades, initial_account),
        regime_performance=regime_rows,
        dimension_attribution=compute_dimension_attribution(trades),
        monte_carlo=monte_carlo,
        per_asset_metrics=_per_asset_metrics(trades, initial_account, backtest_bars),
        verdict=verdict,
        verdict_detail=verdict_detail,
    )


def generate_verdict(
    metrics: PerformanceMetrics,
    config: BacktestConfig,
) -> tuple[str, list[str]]:
    """Generate plain-English verdict and supporting bullet points."""
    account_size = config.risk.account_size_usd
    largest_loss_pct = (
        abs(metrics.largest_loss_usd) / account_size * _HUNDRED
        if account_size > _ZERO
        else _ZERO
    )
    degraded = metrics.max_drawdown_pct > Decimal("20") or largest_loss_pct > Decimal("5")
    if degraded:
        return "DEGRADED", _verdict_details("DEGRADED", metrics, largest_loss_pct)
    if metrics.profit_factor < Decimal("1"):
        return "NO_EDGE", _verdict_details("NO_EDGE", metrics, largest_loss_pct)
    if (
        metrics.sharpe_ratio > Decimal("1.5")
        and metrics.profit_factor > Decimal("1.5")
        and metrics.win_rate_pct > Decimal("55")
    ):
        return "STRONG_EDGE", _verdict_details("STRONG_EDGE", metrics, largest_loss_pct)
    if metrics.sharpe_ratio > Decimal("1.0") and metrics.profit_factor > Decimal("1.2"):
        return "PROMISING", _verdict_details("PROMISING", metrics, largest_loss_pct)
    if metrics.sharpe_ratio > Decimal("0.5") and metrics.profit_factor > Decimal("1.0"):
        return "MARGINAL", _verdict_details("MARGINAL", metrics, largest_loss_pct)
    return "NO_EDGE", _verdict_details("NO_EDGE", metrics, largest_loss_pct)


def _verdict_details(
    verdict: str,
    metrics: PerformanceMetrics,
    largest_loss_pct: Decimal,
) -> list[str]:
    return [
        f"Verdict={verdict} from sharpe={metrics.sharpe_ratio} and profit_factor={metrics.profit_factor}.",
        f"Win rate={metrics.win_rate_pct}% across {metrics.total_trades} closed trades.",
        f"Total PnL={metrics.total_pnl_usd} USD ({metrics.total_pnl_pct}% of account).",
        f"Max drawdown={metrics.max_drawdown_pct}% with largest single loss={largest_loss_pct}% of account.",
        f"Expectancy={metrics.expectancy_usd} USD per trade with Kelly cap={metrics.kelly_fraction}.",
    ]


def _serialise_equity_curve(equity_df: pl.DataFrame) -> list[dict[str, object]]:
    if equity_df.is_empty():
        return []
    return [
        {
            "bar_index": int(row["bar_index"]),
            "equity_usd": str(row["equity_usd"]),
            "drawdown_pct": str(row["drawdown_pct"]),
            "drawdown_usd": str(row["drawdown_usd"]),
        }
        for row in equity_df.iter_rows(named=True)
    ]


def _per_asset_metrics(
    trades: list[ClosedTrade],
    initial_account: Decimal,
    backtest_bars: int,
) -> dict[str, PerformanceMetrics]:
    assets = sorted({trade.entry.asset for trade in trades})
    result: dict[str, PerformanceMetrics] = {}
    for asset in assets:
        asset_trades = [trade for trade in trades if trade.entry.asset == asset]
        result[asset] = compute_all_metrics(asset_trades, initial_account, backtest_bars)
    return result
