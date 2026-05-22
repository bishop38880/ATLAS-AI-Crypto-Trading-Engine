"""Tests for backtesting analytics engine (BT-03)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import polars as pl
import pytest

from backtesting.analytics.metrics import (
    compute_all_metrics,
    compute_equity_curve,
    compute_max_drawdown,
    compute_monthly_returns,
    compute_profit_factor,
    compute_sharpe,
    compute_threshold_analysis,
)
from backtesting.analytics.monte_carlo import MonteCarloConfig, run_monte_carlo
from backtesting.analytics.result import build_backtest_result, generate_verdict
from backtesting.analytics.metrics import PerformanceMetrics
from backtesting.engine.config import BacktestConfig
from backtesting.engine.position import ClosedTrade, TradeEntry, TradeExit
from backtesting.engine.replay import BacktestReplay


def _timestamp_for_month(year: int, month: int, day: int = 15) -> str:
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat()


def _closed_trade(
    *,
    trade_id: str = "t1",
    score: int = 150,
    pnl_usd: Decimal = Decimal("100"),
    exit_reason: str = "TAKE_PROFIT",
    exit_time: str = "2024-03-15T00:00:00+00:00",
    entry_bar: int = 10,
    exit_bar: int = 20,
) -> ClosedTrade:
    entry = TradeEntry(
        trade_id=trade_id,
        asset="BTCUSDT",
        direction="LONG",
        entry_bar_index=entry_bar,
        entry_timestamp_utc="2024-03-01T00:00:00+00:00",
        entry_price=Decimal("100"),
        entry_price_with_slippage=Decimal("100.01"),
        position_size_usd=Decimal("500"),
        leverage=5,
        stop_loss=Decimal("90"),
        take_profit=Decimal("120"),
        score_at_entry=score,
        derivatives_score=40,
        technical_score=30,
        market_context_score=20,
        signal_class="BUY",
    )
    trade_exit = TradeExit(
        trade_id=trade_id,
        exit_bar_index=exit_bar,
        exit_timestamp_utc=exit_time,
        exit_price=Decimal("110"),
        exit_price_with_slippage=Decimal("109.9"),
        exit_reason=exit_reason,
        pnl_usd=pnl_usd,
        pnl_pct=Decimal("2"),
        duration_bars=exit_bar - entry_bar,
    )
    return ClosedTrade(entry=entry, exit=trade_exit)


class TestComputeSharpe:
    """Sharpe ratio behaves on known return series."""

    def test_positive_net_returns_produce_positive_sharpe(self) -> None:
        returns = pl.Series(
            "returns",
            [Decimal("0.01"), Decimal("-0.005"), Decimal("0.02"), Decimal("0.003")],
            dtype=pl.Decimal(precision=20, scale=8),
        )
        sharpe = compute_sharpe(returns, bars_per_year=252)
        assert sharpe > Decimal("0")


class TestComputeMaxDrawdown:
    """Max drawdown extracts peak-to-trough metrics."""

    def test_manual_equity_curve_drawdown(self) -> None:
        equity_curve = pl.DataFrame(
            {
                "bar_index": [1, 2, 3, 4],
                "equity_usd": [
                    Decimal("10000"),
                    Decimal("11000"),
                    Decimal("9000"),
                    Decimal("10500"),
                ],
                "drawdown_pct": [
                    Decimal("0"),
                    Decimal("0"),
                    Decimal("18.181818"),
                    Decimal("4.545454"),
                ],
                "drawdown_usd": [
                    Decimal("0"),
                    Decimal("0"),
                    Decimal("2000"),
                    Decimal("500"),
                ],
            },
        )
        max_dd_pct, duration = compute_max_drawdown(equity_curve)
        assert max_dd_pct >= Decimal("18")
        assert duration >= 0


class TestComputeProfitFactor:
    """Profit factor handles zero-loss edge case."""

    def test_zero_losses_returns_infinity(self) -> None:
        trades = [_closed_trade(pnl_usd=Decimal("50")), _closed_trade(trade_id="t2", pnl_usd=Decimal("25"))]
        assert compute_profit_factor(trades) == Decimal("Infinity")


class TestGenerateVerdict:
    """Verdict classifier covers all five outcomes."""

    def _metrics(self, **overrides: object) -> PerformanceMetrics:
        base = compute_all_metrics([], Decimal("10000"), 1000)
        payload = base.model_dump()
        payload.update(overrides)
        return PerformanceMetrics(**payload)

    def test_strong_edge_verdict(self) -> None:
        metrics = self._metrics(
            sharpe_ratio=Decimal("2.0"),
            profit_factor=Decimal("2.0"),
            win_rate_pct=Decimal("60"),
            max_drawdown_pct=Decimal("5"),
            largest_loss_usd=Decimal("-100"),
        )
        verdict, _ = generate_verdict(metrics, BacktestConfig())
        assert verdict == "STRONG_EDGE"

    def test_promising_verdict(self) -> None:
        metrics = self._metrics(
            sharpe_ratio=Decimal("1.2"),
            profit_factor=Decimal("1.3"),
            max_drawdown_pct=Decimal("8"),
            largest_loss_usd=Decimal("-100"),
        )
        verdict, _ = generate_verdict(metrics, BacktestConfig())
        assert verdict == "PROMISING"

    def test_marginal_verdict(self) -> None:
        metrics = self._metrics(
            sharpe_ratio=Decimal("0.7"),
            profit_factor=Decimal("1.1"),
            max_drawdown_pct=Decimal("8"),
            largest_loss_usd=Decimal("-100"),
        )
        verdict, _ = generate_verdict(metrics, BacktestConfig())
        assert verdict == "MARGINAL"

    def test_no_edge_verdict(self) -> None:
        metrics = self._metrics(
            profit_factor=Decimal("0.8"),
            sharpe_ratio=Decimal("0.1"),
            max_drawdown_pct=Decimal("8"),
            largest_loss_usd=Decimal("-100"),
        )
        verdict, _ = generate_verdict(metrics, BacktestConfig())
        assert verdict == "NO_EDGE"

    def test_degraded_verdict(self) -> None:
        metrics = self._metrics(
            sharpe_ratio=Decimal("2.0"),
            profit_factor=Decimal("2.0"),
            win_rate_pct=Decimal("60"),
            max_drawdown_pct=Decimal("25"),
            largest_loss_usd=Decimal("-100"),
        )
        verdict, _ = generate_verdict(metrics, BacktestConfig())
        assert verdict == "DEGRADED"


class TestComputeMonthlyReturns:
    """Monthly returns group by exit month."""

    def test_groups_by_calendar_month(self) -> None:
        trades = [
            _closed_trade(
                trade_id="jan",
                exit_time=_timestamp_for_month(2024, 1),
                pnl_usd=Decimal("100"),
            ),
            _closed_trade(
                trade_id="feb",
                exit_time=_timestamp_for_month(2024, 2),
                pnl_usd=Decimal("-50"),
            ),
            _closed_trade(
                trade_id="feb-2",
                exit_time=_timestamp_for_month(2024, 2, day=20),
                pnl_usd=Decimal("25"),
            ),
        ]
        monthly = compute_monthly_returns(trades, Decimal("10000"))
        assert len(monthly) == 2
        assert monthly[0].month == 1
        assert monthly[1].month == 2
        assert monthly[1].trade_count == 2


class TestComputeThresholdAnalysis:
    """Threshold bins partition trades by entry score."""

    def test_bins_trades_into_score_buckets(self) -> None:
        trades = [
            _closed_trade(trade_id="weak", score=130),
            _closed_trade(trade_id="buy", score=155),
            _closed_trade(trade_id="strong", score=190),
        ]
        analysis = compute_threshold_analysis(
            trades,
            Decimal("10000"),
            bins=[(120, 139), (150, 169), (180, 199)],
        )
        assert analysis[0].trade_count == 1
        assert analysis[1].trade_count == 1
        assert analysis[2].trade_count == 1


class TestMonteCarlo:
    """Monte Carlo simulation returns percentile summary."""

    def test_monte_carlo_runs_configured_simulations(self) -> None:
        trades = [_closed_trade(pnl_usd=Decimal("50")) for _ in range(20)]
        result = run_monte_carlo(
            trades,
            Decimal("10000"),
            MonteCarloConfig(n_simulations=50, seed=7),
        )
        assert result.n_simulations == 50
        assert result.p95_total_return_pct >= result.p5_total_return_pct


class TestAnalyticsIntegration:
    """Replay pipeline feeds analytics and verdict generation."""

    def test_replay_to_metrics_and_verdict(self) -> None:
        config = BacktestConfig(
            assets=["BTCUSDT"],
            start_date="2024-01-01",
            end_date="2024-02-01",
            use_synthetic=True,
        )
        replay_result = asyncio.run(BacktestReplay().run(config))
        analytics = build_backtest_result(
            trades=replay_result.closed_trades,
            config=config,
            backtest_bars=1200,
        )
        assert analytics.metrics.total_trades == len(replay_result.closed_trades)
        assert analytics.verdict in {
            "STRONG_EDGE",
            "PROMISING",
            "MARGINAL",
            "NO_EDGE",
            "DEGRADED",
        }
        curve = compute_equity_curve(replay_result.closed_trades, config.risk.account_size_usd)
        assert curve.height == len(replay_result.closed_trades) or curve.is_empty()
