"""Markdown summaries for persisted DuckDB backtest runs."""

from __future__ import annotations

from prometheus.backtest.config import BacktestMetrics
from prometheus.backtest.db import BacktestDB


async def render_run_markdown(db: BacktestDB, run_id: str) -> str:
    """Return a markdown document for ``run_id``."""
    run = await db.fetch_run(run_id)
    met = await db.fetch_metrics(run_id)
    if run is None:
        return "# Run not found\n\nUnknown run_id.\n"
    if met is None:
        return "# Metrics missing\n\nRun header exists but metrics row absent.\n"
    lines = [
        "# Backtest run `{}`".format(run_id),
        "",
        "## Parameters",
        "",
        "| Field | Value |",
        "| --- | --- |",
        "| Asset | `{}` |".format(run.asset),
        "| Timeframe | `{}` |".format(run.timeframe),
        "| Start ts | `{}` |".format(run.start_ts),
        "| End ts | `{}` |".format(run.end_ts),
        "| Initial capital | `{}` |".format(run.initial_capital),
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | --- |",
        "| Total trades | `{}` |".format(met.total_trades),
        "| Win rate | `{:.4f}` |".format(met.win_rate),
        "| Net PnL | `{}` |".format(met.net_pnl),
        "| Max drawdown | `{}` |".format(met.max_drawdown),
        "| Sharpe | `{}` |".format(met.sharpe_ratio),
        "| Sortino | `{}` |".format(met.sortino_ratio),
        "| Profit factor | `{}` |".format(met.profit_factor),
        "| Vetoes | `{}` |".format(met.veto_count),
        "",
    ]
    return "\n".join(lines)


def metrics_table_text(run_id: str, met: BacktestMetrics) -> str:
    """Plain-text table for CLI stdout."""
    rows = [
        ("run_id", run_id),
        ("total_trades", met.total_trades),
        ("win_rate", met.win_rate),
        ("net_pnl", met.net_pnl),
        ("max_drawdown", met.max_drawdown),
        ("sharpe_ratio", met.sharpe_ratio),
        ("veto_count", met.veto_count),
    ]
    w = max(len(str(a)) for a, _ in rows)
    lines = ["{} : {}".format(str(k).ljust(w), v) for k, v in rows]
    return "\n".join(lines)
