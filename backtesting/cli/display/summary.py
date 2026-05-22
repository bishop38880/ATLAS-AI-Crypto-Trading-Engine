"""Rich panels and tables for backtest analytics output."""

from __future__ import annotations

from decimal import Decimal

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from backtesting.analytics.metrics import ThresholdAnalysis
from backtesting.analytics.regime import RegimePerformance
from backtesting.analytics.result import BacktestResult
from backtesting.cli.display.charts import render_equity_curve, render_monthly_heatmap
from backtesting.engine.config import BacktestConfig

_VERDICT_STYLES: dict[str, str] = {
    "STRONG_EDGE": "bold bright_green",
    "PROMISING": "green",
    "MARGINAL": "yellow",
    "NO_EDGE": "red",
    "DEGRADED": "bold bright_red",
}


def verdict_style(verdict: str) -> str:
    """Return Rich style string for a verdict label."""
    return _VERDICT_STYLES.get(verdict, "white")


def render_config_panel(config: BacktestConfig, console: Console) -> None:
    """Show backtest configuration summary."""
    days = _estimate_period_days(config.start_date, config.end_date)
    body = "\n".join(
        [
            f"Assets:       {', '.join(config.assets)}",
            f"Period:       {config.start_date} → {config.end_date} ({days} days)",
            f"Timeframe:    {config.timeframe}",
            f"Account:      ${config.risk.account_size_usd:,}",
            (
                f"Thresholds:   WEAK ≥{config.thresholds.weak}  "
                f"BUY ≥{config.thresholds.buy}  STRONG ≥{config.thresholds.strong}"
            ),
            f"Walk-Forward: {'enabled' if config.walk_forward else 'disabled'}",
            f"Synthetic:    {'yes' if config.use_synthetic else 'no'}",
        ],
    )
    console.print(Panel(body, title="Backtest Configuration", border_style="cyan"))


def render_full_result(result: BacktestResult, console: Console) -> None:
    """Render the complete analytics display sequence."""
    render_verdict_banner(result, console)
    render_metrics_table(result, console)
    render_threshold_table(result.threshold_analysis, console)
    console.print(render_monthly_heatmap(result.monthly_returns))
    render_regime_table(result.regime_performance, console)
    console.print(Panel(render_equity_curve(result.equity_curve), title="Equity Curve"))
    if result.monte_carlo is not None:
        render_monte_carlo_panel(result, console)


def render_verdict_banner(result: BacktestResult, console: Console) -> None:
    """Show colour-coded verdict panel."""
    style = verdict_style(result.verdict)
    lines = [f"● {detail}" for detail in result.verdict_detail[:4]]
    title = f"BACKTEST VERDICT: {result.verdict}"
    console.print(Panel("\n".join(lines), title=title, border_style=style))


def render_metrics_table(result: BacktestResult, console: Console) -> None:
    """Show core performance metrics."""
    metrics = result.metrics
    table = Table(title="Core Metrics", show_header=True)
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    rows = [
        ("Total Return", f"{metrics.total_pnl_pct:+.1f}%"),
        ("Annualised Return", f"{metrics.annualised_return_pct:+.1f}%"),
        ("Sharpe Ratio", f"{metrics.sharpe_ratio:.2f}"),
        ("Sortino Ratio", f"{metrics.sortino_ratio:.2f}"),
        ("Calmar Ratio", f"{metrics.calmar_ratio:.2f}"),
        ("Max Drawdown", f"{metrics.max_drawdown_pct:.1f}%"),
        ("Max DD Duration", f"{metrics.max_drawdown_duration_bars} bars"),
        ("Total Trades", str(metrics.total_trades)),
        ("Win Rate", f"{metrics.win_rate_pct:.1f}%"),
        ("Profit Factor", f"{metrics.profit_factor:.2f}"),
        ("Avg Win", f"${metrics.avg_win_usd:,.0f}"),
        ("Avg Loss", f"${metrics.avg_loss_usd:,.0f}"),
        ("Expectancy", f"${metrics.expectancy_usd:+,.0f}/trade"),
        ("Kelly Fraction", f"{metrics.kelly_fraction * 100:.1f}%"),
    ]
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


def render_threshold_table(buckets: list[ThresholdAnalysis], console: Console) -> None:
    """Show score threshold performance breakdown."""
    table = Table(title="Score Threshold Analysis")
    table.add_column("Score Range")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("Profit Factor", justify="right")
    table.add_column("Avg P&L", justify="right")
    for bucket in buckets:
        label = _threshold_label(bucket.min_score, bucket.max_score)
        table.add_row(
            label,
            str(bucket.trade_count),
            f"{bucket.win_rate_pct:.1f}%",
            f"{bucket.profit_factor:.2f}",
            f"${bucket.avg_pnl_usd:+,.0f}",
        )
    console.print(table)
    console.print(_threshold_insight(buckets))


def render_regime_table(rows: list[RegimePerformance], console: Console) -> None:
    """Show regime-stratified performance."""
    table = Table(title="Regime Breakdown")
    table.add_column("Regime")
    table.add_column("Trades", justify="right")
    table.add_column("Win Rate", justify="right")
    table.add_column("Profit Factor", justify="right")
    for row in rows:
        warning = "  ⚠" if row.profit_factor < Decimal("1") else ""
        table.add_row(
            row.regime,
            str(row.trade_count),
            f"{row.win_rate_pct:.1f}%",
            f"{row.profit_factor:.2f}{warning}",
        )
    console.print(table)


def render_monte_carlo_panel(result: BacktestResult, console: Console) -> None:
    """Show Monte Carlo summary statistics."""
    mc = result.monte_carlo
    if mc is None:
        return
    body = (
        f"Monte Carlo ({mc.n_simulations:,} simulations)\n"
        f"  Median return:   {mc.median_total_return_pct:+.1f}%      "
        f"Probability of loss:  {mc.probability_of_loss_pct:.1f}%\n"
        f"  5th percentile:  {mc.p5_total_return_pct:+.1f}%      "
        f"P(drawdown > 15%):    {mc.probability_exceed_10pct_dd_pct:.1f}%"
    )
    console.print(Panel(body, title="Monte Carlo", border_style="magenta"))


def render_compact_summary(result: BacktestResult, console: Console) -> None:
    """Show a minimal three-metric summary for tutorial chapter 4."""
    metrics = result.metrics
    body = "\n".join(
        [
            f"Trades:       {metrics.total_trades}",
            f"Win Rate:     {metrics.win_rate_pct:.0f}%",
            f"Total Return: {metrics.total_pnl_pct:+.1f}%",
        ],
    )
    console.print(Panel(body, title="Your first backtest is done!", border_style="green"))


def _threshold_label(min_score: int, max_score: int) -> str:
    if max_score >= 180:
        tier = "STRONG"
    elif max_score >= 150:
        tier = "BUY"
    elif max_score >= 120:
        tier = "WEAK"
    else:
        tier = "OTHER"
    return f"{min_score}–{max_score} ({tier})"


def _threshold_insight(buckets: list[ThresholdAnalysis]) -> str:
    strong = next((b for b in buckets if b.min_score >= 180), None)
    weak = next((b for b in buckets if b.min_score <= 120 and b.max_score < 150), None)
    if strong and weak and weak.profit_factor > strong.profit_factor:
        return "[yellow]⚠ Investigate scoring calibration — WEAK outperforms STRONG[/]"
    return "[green]💡 Higher conviction scores produce measurably better outcomes[/]"


def _estimate_period_days(start: str, end: str) -> int:
    from datetime import date

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    return max((end_date - start_date).days, 0)
