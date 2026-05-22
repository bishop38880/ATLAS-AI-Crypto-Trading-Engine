"""`backtesting run` command implementation."""

from __future__ import annotations

import argparse
from decimal import Decimal

from rich.console import Console

from backtesting.analytics.exporter import BacktestExporter
from backtesting.cli.display.progress import build_replay_progress, make_progress_callback
from backtesting.cli.display.summary import render_config_panel, render_full_result
from backtesting.cli.runner import parse_asset_list, parse_threshold_triplet, run_backtest_pipeline
from backtesting.engine.config import BacktestConfig, RiskConfig, ScoreThresholds

console = Console()


def register_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `run` subcommand."""
    parser = subparsers.add_parser("run", help="Run a backtest with specified configuration")
    parser.add_argument("--assets", default="BTCUSDT", help="Comma-separated symbols")
    parser.add_argument("--start", default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2025-01-01", help="End date YYYY-MM-DD")
    parser.add_argument("--timeframe", default="1h", choices=["1h", "4h", "1d"])
    parser.add_argument("--account", type=Decimal, default=Decimal("10000"))
    parser.add_argument("--thresholds", default=None, help="weak,buy,strong e.g. 120,150,180")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--monte-carlo", action="store_true")
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic data (offline)")
    parser.add_argument("--export", default=None, help="Export JSON results path")
    parser.add_argument("--description", default="")


def build_config_from_args(args: argparse.Namespace) -> BacktestConfig:
    """Map CLI arguments to BacktestConfig."""
    weak, buy, strong = parse_threshold_triplet(args.thresholds)
    return BacktestConfig(
        assets=parse_asset_list(args.assets),
        start_date=args.start,
        end_date=args.end,
        timeframe=args.timeframe,
        thresholds=ScoreThresholds(weak=weak, buy=buy, strong=strong),
        risk=RiskConfig(account_size_usd=args.account),
        use_synthetic=args.synthetic,
        walk_forward=args.walk_forward,
        description=args.description,
    )


async def execute(args: argparse.Namespace) -> None:
    """Run the configured backtest and print results."""
    config = build_config_from_args(args)
    render_config_panel(config, console)
    trade_counter = [0]
    progress = build_replay_progress(console)
    task_id = progress.add_task("Starting replay", total=100, trade_detail="")

    def on_progress(completed: int, total: int, asset: str) -> None:
        trade_counter[0] = max(trade_counter[0], completed // 50)
        callback = make_progress_callback(progress, task_id, trade_counter)
        callback(completed, total, asset)

    with progress:
        result = await run_backtest_pipeline(
            config,
            monte_carlo=args.monte_carlo,
            on_progress=on_progress,
        )
    render_full_result(result, console)
    if args.export:
        await _export_result(result, args.export)


async def _export_result(result: object, export_path: str) -> None:
    import asyncio as asyncio_module

    exporter = BacktestExporter()
    await asyncio_module.to_thread(exporter.to_json, result, export_path)
    console.print(f"[green]Exported results to {export_path}[/]")
