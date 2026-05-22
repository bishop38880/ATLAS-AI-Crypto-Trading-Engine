"""Backtesting performance analytics (BT-03)."""

from __future__ import annotations

from backtesting.analytics.attribution import DimensionAttribution, compute_dimension_attribution
from backtesting.analytics.exporter import BacktestExporter
from backtesting.analytics.metrics import (
    MonthlyReturns,
    PerformanceMetrics,
    ThresholdAnalysis,
    compute_all_metrics,
    compute_equity_curve,
    compute_kelly,
    compute_max_drawdown,
    compute_monthly_returns,
    compute_profit_factor,
    compute_sharpe,
    compute_sortino,
    compute_threshold_analysis,
)
from backtesting.analytics.monte_carlo import MonteCarloConfig, MonteCarloResult, run_monte_carlo
from backtesting.analytics.regime import RegimePerformance, classify_bar_regime, compute_regime_breakdown
from backtesting.analytics.result import BacktestResult, build_backtest_result, generate_verdict

__all__ = [
    "BacktestExporter",
    "BacktestResult",
    "DimensionAttribution",
    "MonteCarloConfig",
    "MonteCarloResult",
    "MonthlyReturns",
    "PerformanceMetrics",
    "RegimePerformance",
    "ThresholdAnalysis",
    "build_backtest_result",
    "classify_bar_regime",
    "compute_all_metrics",
    "compute_dimension_attribution",
    "compute_equity_curve",
    "compute_kelly",
    "compute_max_drawdown",
    "compute_monthly_returns",
    "compute_profit_factor",
    "compute_regime_breakdown",
    "compute_sharpe",
    "compute_sortino",
    "compute_threshold_analysis",
    "generate_verdict",
    "run_monte_carlo",
]
