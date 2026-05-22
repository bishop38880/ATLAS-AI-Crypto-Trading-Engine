"""Persistence and export helpers for analytics results."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import msgspec

from backtesting.analytics.result import BacktestResult
from backtesting.data.db import BacktestDB


class BacktestExporter:
    """Persists and exports backtest analytics results."""

    def save_to_db(self, result: BacktestResult, db: BacktestDB) -> None:
        """Save result summary to the backtest_runs DuckDB table."""
        summary_payload = {
            "verdict": result.verdict,
            "verdict_detail": result.verdict_detail,
            "metrics": result.metrics.model_dump(mode="json"),
            "total_trades": result.metrics.total_trades,
        }
        config_json = msgspec.json.encode(result.config.model_dump(mode="json")).decode()
        summary_json = msgspec.json.encode(summary_payload).decode()
        db._connection.execute(
            """
            INSERT OR REPLACE INTO backtest_runs
            (run_id, created_at, config_json, status, summary_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                result.config.run_id,
                datetime.now(timezone.utc),
                config_json,
                "COMPLETED",
                summary_json,
            ],
        )

    def to_json(self, result: BacktestResult, path: str) -> None:
        """Export full analytics result to a JSON file."""
        payload = result.model_dump(mode="json")
        encoded = msgspec.json.encode(payload)
        Path(path).write_bytes(encoded)

    def to_csv_trades(self, result: BacktestResult, path: str) -> None:
        """Export closed trades to CSV."""
        lines = [
            "trade_id,asset,direction,entry_bar,exit_bar,pnl_usd,pnl_pct,exit_reason,score",
        ]
        for trade in result.trades:
            lines.append(
                ",".join(
                    [
                        trade.entry.trade_id,
                        trade.entry.asset,
                        trade.entry.direction,
                        str(trade.entry.entry_bar_index),
                        str(trade.exit.exit_bar_index),
                        str(trade.exit.pnl_usd),
                        str(trade.exit.pnl_pct),
                        trade.exit.exit_reason,
                        str(trade.entry.score_at_entry),
                    ],
                ),
            )
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def to_csv_equity(self, result: BacktestResult, path: str) -> None:
        """Export equity curve rows to CSV."""
        lines = ["bar_index,equity_usd,drawdown_pct,drawdown_usd"]
        for row in result.equity_curve:
            lines.append(
                ",".join(
                    [
                        str(row["bar_index"]),
                        str(row["equity_usd"]),
                        str(row["drawdown_pct"]),
                        str(row["drawdown_usd"]),
                    ],
                ),
            )
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")

    def generate_report(self, result: BacktestResult) -> str:
        """Generate a markdown report with key analytics tables."""
        metrics = result.metrics
        lines = [
            "# Backtest Analytics Report",
            "",
            f"- Run ID: `{result.config.run_id}`",
            f"- Completed: {result.completed_at}",
            f"- Verdict: **{result.verdict}**",
            "",
            "## Summary Metrics",
            "",
            "| Metric | Value |",
            "| --- | --- |",
            f"| Total PnL (USD) | {metrics.total_pnl_usd} |",
            f"| Total PnL (%) | {metrics.total_pnl_pct} |",
            f"| Sharpe | {metrics.sharpe_ratio} |",
            f"| Sortino | {metrics.sortino_ratio} |",
            f"| Profit Factor | {metrics.profit_factor} |",
            f"| Win Rate (%) | {metrics.win_rate_pct} |",
            f"| Max Drawdown (%) | {metrics.max_drawdown_pct} |",
            "",
            "## Verdict Detail",
            "",
        ]
        lines.extend(f"- {detail}" for detail in result.verdict_detail)
        lines.extend(["", "## Threshold Analysis", "", "| Range | Trades | Win Rate | Avg PnL |", "| --- | --- | --- | --- |"])
        for bucket in result.threshold_analysis:
            lines.append(
                f"| {bucket.min_score}-{bucket.max_score} | {bucket.trade_count} | "
                f"{bucket.win_rate_pct} | {bucket.avg_pnl_usd} |",
            )
        if result.monte_carlo is not None:
            mc = result.monte_carlo
            lines.extend(
                [
                    "",
                    "## Monte Carlo",
                    "",
                    f"- Median return: {mc.median_total_return_pct}%",
                    f"- P5 return: {mc.p5_total_return_pct}%",
                    f"- P95 return: {mc.p95_total_return_pct}%",
                    f"- P95 drawdown: {mc.p95_max_drawdown_pct}%",
                    f"- Probability of loss: {mc.probability_of_loss_pct}%",
                ],
            )
        return "\n".join(lines) + "\n"
