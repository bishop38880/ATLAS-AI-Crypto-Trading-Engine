"""CLI entry point for backtesting data ingestion."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from loguru import logger
from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from backtesting.data.bitget_fetcher import (
    BitgetAssetNotFoundError,
    BitgetHistoricalFetcher,
    BitgetNoDataError,
    default_universe_assets,
)
from backtesting.data.db import BacktestDB

console = Console()


def build_parser() -> argparse.ArgumentParser:
    """Build the ingestion CLI argument parser."""
    parser = argparse.ArgumentParser(description="POLARIS backtesting data ingestion")
    parser.add_argument("--assets", default="all", help="Comma list or 'all'")
    parser.add_argument("--start", default="2024-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--timeframe", default="1h", choices=["1h", "4h", "1d"])
    parser.add_argument("--status", action="store_true", help="Show DB coverage only")
    parser.add_argument("--no-funding", action="store_true", help="Skip funding fetch")
    return parser


def resolve_ingest_assets(raw_value: str) -> list[str]:
    """Resolve CLI asset list or the full universe."""
    if raw_value.strip().lower() == "all":
        return default_universe_assets()
    return [item.strip().upper() for item in raw_value.split(",") if item.strip()]


async def ingest_assets(
    *,
    assets: list[str],
    start_date: str,
    end_date: str,
    timeframe: str = "1h",
    skip_funding: bool = False,
    synthetic_fallback: bool = False,
    console: Console | None = None,
) -> None:
    """Fetch and persist OHLCV and funding for the given assets."""
    output_console = console or Console()
    fetcher = BitgetHistoricalFetcher()
    db = BacktestDB.instance()
    progress = Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TimeRemainingColumn(),
        console=output_console,
    )
    task_id = progress.add_task("Fetching assets", total=len(assets))
    api_unavailable = False
    try:
        with progress:
            for index, asset in enumerate(assets, start=1):
                progress.update(
                    task_id,
                    description=f"Fetching [{index}/{len(assets)}: {asset}]",
                )
                try:
                    await _ingest_single_asset(
                        fetcher=fetcher,
                        db=db,
                        asset=asset,
                        timeframe=timeframe,
                        start_date=start_date,
                        end_date=end_date,
                        skip_funding=skip_funding,
                    )
                except (BitgetAssetNotFoundError, BitgetNoDataError) as exc:
                    logger.warning("ingest_asset_skipped | asset={} | err={}", asset, exc)
                except Exception as exc:
                    api_unavailable = True
                    logger.warning("ingest_fetch_failed | asset={} | err={}", asset, exc)
                progress.advance(task_id)
    finally:
        await fetcher.close()
    if api_unavailable and synthetic_fallback:
        output_console.print(
            "[yellow]Bitget API unavailable — use `python -m backtesting run --synthetic` "
            "or enable tutorial mode for offline data.[/]",
        )


async def _ingest_single_asset(
    *,
    fetcher: BitgetHistoricalFetcher,
    db: BacktestDB,
    asset: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    skip_funding: bool,
) -> None:
    ohlcv = await fetcher.fetch_ohlcv(asset, timeframe, start_date, end_date)
    await asyncio.to_thread(db.write_ohlcv, ohlcv)
    if skip_funding:
        return
    funding = await fetcher.fetch_funding_rates(asset)
    await asyncio.to_thread(db.write_funding_rates, funding)


async def _ingest_assets(args: argparse.Namespace) -> None:
    assets = resolve_ingest_assets(args.assets)
    end_date = args.end or date.today().isoformat()
    await ingest_assets(
        assets=assets,
        start_date=args.start,
        end_date=end_date,
        timeframe=args.timeframe,
        skip_funding=args.no_funding,
        console=console,
    )
    _print_coverage_table(BacktestDB.instance())


def _print_coverage_table(db: BacktestDB) -> None:
    table = Table(title="Backtesting Data Coverage")
    table.add_column("Asset")
    table.add_column("Start")
    table.add_column("End")
    table.add_column("Bars")
    table.add_column("Funding Bars")
    table.add_column("Status")

    for row in db.get_coverage():
        status = "OK" if row.ohlcv_bar_count > 0 else "MISSING"
        table.add_row(
            row.asset,
            row.ohlcv_start or "-",
            row.ohlcv_end or "-",
            str(row.ohlcv_bar_count),
            str(row.funding_bar_count),
            status,
        )
    console.print(table)


def _print_status(db: BacktestDB) -> None:
    _print_coverage_table(db)


async def main_async() -> None:
    """Async CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()
    if args.status:
        db = BacktestDB.instance()
        _print_status(db)
        return
    await _ingest_assets(args)


def main() -> None:
    """Run the ingestion CLI."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
