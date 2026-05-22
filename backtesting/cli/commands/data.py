"""`backtesting data` subcommands — ingest and status."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from rich.console import Console
from rich.table import Table

from backtesting.data.constants import POLARIS_UNIVERSE_ASSETS
from backtesting.data.db import BacktestDB
from backtesting.data.ingest import ingest_assets, resolve_ingest_assets

console = Console()


def register_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the `data` command group."""
    parser = subparsers.add_parser("data", help="Manage historical data")
    data_sub = parser.add_subparsers(dest="data_command", required=True)
    ingest = data_sub.add_parser("ingest", help="Fetch and store OHLCV + funding")
    ingest.add_argument("--assets", default="all")
    ingest.add_argument("--start", default="2024-01-01")
    ingest.add_argument("--end", default=None)
    ingest.add_argument("--timeframe", default="1h", choices=["1h", "4h", "1d"])
    ingest.add_argument("--no-funding", action="store_true")
    ingest.add_argument("--synthetic-fallback", action="store_true")
    data_sub.add_parser("status", help="Show database coverage")


async def execute(args: argparse.Namespace) -> None:
    """Dispatch data ingest or status."""
    if args.data_command == "status":
        print_coverage_status()
        return
    if args.data_command == "ingest":
        await _run_ingest(args)
        return
    raise ValueError(f"Unknown data subcommand: {args.data_command}")


async def _run_ingest(args: argparse.Namespace) -> None:
    assets = resolve_ingest_assets(args.assets)
    end_date = args.end or date.today().isoformat()
    await ingest_assets(
        assets=assets,
        start_date=args.start,
        end_date=end_date,
        timeframe=args.timeframe,
        skip_funding=args.no_funding,
        synthetic_fallback=args.synthetic_fallback,
        console=console,
    )
    print_coverage_status()


def print_coverage_status() -> None:
    """Render universe coverage table."""
    db = BacktestDB.instance()
    coverage_by_asset = {row.asset: row for row in db.get_coverage()}
    table = Table(title="Backtesting Data Coverage")
    table.add_column("Asset")
    table.add_column("OHLCV Start")
    table.add_column("OHLCV End")
    table.add_column("Bars", justify="right")
    table.add_column("Funding Bars", justify="right")
    table.add_column("Status")
    ready_count = 0
    for asset in POLARIS_UNIVERSE_ASSETS:
        row = coverage_by_asset.get(asset)
        status = _coverage_status(row)
        if status.startswith("✓"):
            ready_count += 1
        table.add_row(
            asset,
            _format_date(row.ohlcv_start if row else None),
            _format_date(row.ohlcv_end if row else None),
            str(row.ohlcv_bar_count if row else 0),
            str(row.funding_bar_count if row else 0),
            status,
        )
    console.print(table)
    total = len(POLARIS_UNIVERSE_ASSETS)
    console.print(
        f"\n{ready_count}/{total} assets ready for full coverage  ·  "
        "Run `python -m backtesting data ingest` to populate missing",
    )


def _coverage_status(row: object | None) -> str:
    if row is None:
        return "✗ Missing"
    bar_count = getattr(row, "ohlcv_bar_count", 0)
    funding_count = getattr(row, "funding_bar_count", 0)
    if bar_count <= 0:
        return "✗ Missing"
    if funding_count <= 0:
        return "⚠ Partial"
    return "✓ Ready"


def _format_date(value: str | None) -> str:
    if value is None:
        return "─"
    return value[:10]
