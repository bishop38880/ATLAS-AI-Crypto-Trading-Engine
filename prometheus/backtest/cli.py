"""Typer CLI for offline DuckDB backtests — no live I/O."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import typer
from loguru import logger

from prometheus.backtest.config import BacktestConfig
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.engine import BacktestEngine
from prometheus.backtest.loader import import_candles_from_csv, signal_payload_to_row
from prometheus.backtest.report import metrics_table_text, render_run_markdown

app = typer.Typer(no_args_is_help=True)


def _day_start_ms(iso_day: str) -> int:
    dt = datetime.strptime(iso_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _day_end_ms(iso_day: str) -> int:
    dt = datetime.strptime(iso_day, "%Y-%m-%d").replace(
        hour=23,
        minute=59,
        second=59,
        microsecond=999000,
        tzinfo=timezone.utc,
    )
    return int(dt.timestamp() * 1000)


@app.command("run")
def run_backtest(
    asset: str = typer.Option(..., "--asset"),
    timeframe: str = typer.Option(..., "--timeframe"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    capital: str = typer.Option("10000", "--capital"),
    score_threshold: float = typer.Option(65.0, "--score-threshold"),
    db_path: Path | None = typer.Option(None, "--db-path"),
) -> None:
    """Replay signals between date bounds and persist metrics."""

    async def _inner() -> None:
        capital_dec = Decimal(capital)
        if db_path is None:
            cfg = BacktestConfig(
                initial_capital_usd=capital_dec,
                score_threshold=score_threshold,
            )
        else:
            cfg = BacktestConfig(
                db_path=db_path,
                initial_capital_usd=capital_dec,
                score_threshold=score_threshold,
            )
        db = BacktestDB(cfg.db_path)
        await db.init_schema()
        eng = BacktestEngine(cfg, db)
        s_ts = _day_start_ms(start)
        e_ts = _day_end_ms(end)
        result = await eng.run(asset, timeframe, s_ts, e_ts)
        typer.echo(result.run_id)
        typer.echo(metrics_table_text(result.run_id, result.metrics))
        db.close()

    asyncio.run(_inner())


@app.command("import-candles")
def import_candles(
    asset: str = typer.Option(..., "--asset"),
    timeframe: str = typer.Option(..., "--timeframe"),
    file: Path = typer.Option(..., "--file"),
    db_path: Path | None = typer.Option(None, "--db-path"),
) -> None:
    """Bulk-load OHLCV CSV into DuckDB."""

    async def _inner() -> None:
        cfg = BacktestConfig(db_path=db_path) if db_path is not None else BacktestConfig()
        db = BacktestDB(cfg.db_path)
        await db.init_schema()
        n = await import_candles_from_csv(db, file, asset, timeframe)
        logger.info("import_candles_complete | rows={}", n)
        typer.echo(str(n))
        db.close()

    asyncio.run(_inner())


@app.command("import-signals")
def import_signals(
    file: Path = typer.Option(..., "--file"),
    db_path: Path | None = typer.Option(None, "--db-path"),
) -> None:
    """Load newline-delimited JSON into ``bt_signals``."""

    async def _inner() -> None:
        cfg = BacktestConfig(db_path=db_path) if db_path is not None else BacktestConfig()
        db = BacktestDB(cfg.db_path)
        await db.init_schema()
        rows = []
        raw_lines = Path(file).read_bytes().splitlines()
        for line in raw_lines:
            if not line.strip():
                continue
            rows.append(signal_payload_to_row(line))
        await db.insert_signals(rows)
        logger.info("import_signals_complete | rows={}", len(rows))
        typer.echo(str(len(rows)))
        db.close()

    asyncio.run(_inner())


@app.command("report")
def report_cmd(
    run_id: str = typer.Option(..., "--run-id"),
    db_path: Path | None = typer.Option(None, "--db-path"),
) -> None:
    """Print markdown summary for a completed run."""

    async def _inner() -> None:
        cfg = BacktestConfig(db_path=db_path) if db_path is not None else BacktestConfig()
        db = BacktestDB(cfg.db_path)
        await db.init_schema()
        md = await render_run_markdown(db, run_id)
        typer.echo(md)
        db.close()

    asyncio.run(_inner())


def main() -> None:
    app()


if __name__ == "__main__":
    main()
