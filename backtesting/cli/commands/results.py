"""`backtesting results` command — list and show past runs."""

from __future__ import annotations

import argparse
from decimal import Decimal

import msgspec
from rich.console import Console
from rich.table import Table

from backtesting.data.db import BacktestDB

console = Console()


def register_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `results` subcommand."""
    parser = subparsers.add_parser("results", help="View results from past runs")
    parser.add_argument("run_id", nargs="?", default=None, help="Optional run ID")


def execute(args: argparse.Namespace) -> None:
    """List recent runs or show one run in full."""
    db = BacktestDB.instance()
    if args.run_id is None:
        _print_run_list(db)
        return
    record = db.get_run_record(args.run_id)
    if record is None:
        console.print(f"[red]Run not found: {args.run_id}[/]")
        return
    _print_run_detail(record)


def _print_run_list(db: BacktestDB) -> None:
    rows = db.list_recent_runs(limit=10)
    table = Table(title="Recent Backtest Runs")
    table.add_column("Run ID")
    table.add_column("Description")
    table.add_column("Assets")
    table.add_column("Period")
    table.add_column("Return", justify="right")
    table.add_column("Verdict")
    if not rows:
        console.print("[yellow]No saved runs yet. Run a backtest first.[/]")
        return
    for row in rows:
        summary = _decode_summary(row.get("summary_json"))
        config = _decode_config(row.get("config_json"))
        metrics = summary.get("metrics", {}) if summary else {}
        return_pct = metrics.get("total_pnl_pct", Decimal("0"))
        table.add_row(
            str(row["run_id"]),
            str(config.get("description", "—") or "—"),
            f"{len(config.get('assets', []))} assets",
            f"{config.get('start_date', '?')} → {config.get('end_date', '?')}",
            f"{return_pct:+.1f}%" if isinstance(return_pct, (int, float, Decimal)) else str(return_pct),
            str(summary.get("verdict", "—") if summary else "—"),
        )
    console.print(table)


def _print_run_detail(record: dict[str, object]) -> None:
    summary_raw = record.get("summary_json")
    if summary_raw is None:
        console.print("[yellow]Run has no stored analytics summary.[/]")
        return
    payload = msgspec.json.decode(str(summary_raw).encode())
    _render_stored_summary(payload, str(record["run_id"]))


def _render_stored_summary(payload: dict[str, object], run_id: str) -> None:
    """Render stored summary metrics when full BacktestResult is unavailable."""
    metrics = payload.get("metrics", {})
    table = Table(title=f"Run {run_id}")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for key in ("total_pnl_pct", "sharpe_ratio", "profit_factor", "win_rate_pct", "total_trades"):
        if key in metrics:
            table.add_row(key, str(metrics[key]))
    console.print(table)
    for detail in payload.get("verdict_detail", []):
        console.print(f"  • {detail}")


def _decode_summary(raw_value: object | None) -> dict[str, object]:
    if raw_value is None:
        return {}
    return msgspec.json.decode(str(raw_value).encode())


def _decode_config(raw_value: object | None) -> dict[str, object]:
    if raw_value is None:
        return {}
    return msgspec.json.decode(str(raw_value).encode())
