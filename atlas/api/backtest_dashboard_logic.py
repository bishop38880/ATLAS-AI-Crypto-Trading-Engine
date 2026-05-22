"""Dashboard backtest orchestration — bridges ATLAS settings to PROMETHEUS DuckDB replay."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from loguru import logger

from atlas.api.schemas import (
    BacktestEquityPoint,
    BacktestInventoryPayload,
    BacktestMetricsPayload,
    BacktestRunCreatedPayload,
    BacktestRunDetailPayload,
    BacktestRunListPayload,
    BacktestRunSummaryItem,
    BacktestSeedPayload,
    BacktestTradeRow,
)
from atlas.shared.config import PolarisSettings
from prometheus.backtest.config import BacktestConfig, BacktestMetrics, BacktestResult, SimulatedTrade
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.engine import BacktestEngine
from prometheus.backtest.loader import import_candles_from_csv, signal_payload_to_row

_DISCLAIMER = (
    "Offline DuckDB replay of imported candles and recorded signals — not live execution. "
    "Fees and slippage follow POLARIS_BT_* defaults. Compare against paper parallel validation "
    "for heuristic Postgres score paths."
)

_SMOKE_CANDLES = Path("prometheus/backtest/smoke_fixtures/test_candles.csv")
_SMOKE_SIGNALS = Path("prometheus/backtest/smoke_fixtures/test_signals.jsonl")
_SMOKE_ASSET = "BTCUSDT"
_SMOKE_TIMEFRAME = "1h"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_db_path(settings: PolarisSettings) -> Path:
    configured = settings.backtest_duckdb_path
    if configured.is_absolute():
        return configured
    return _repo_root() / configured


def build_backtest_config(
    settings: PolarisSettings,
    *,
    initial_capital_usd: Decimal,
    score_threshold: float,
) -> BacktestConfig:
    """Construct PROMETHEUS config from ATLAS settings (no cross-import in prometheus)."""
    return BacktestConfig(
        db_path=_resolve_db_path(settings),
        initial_capital_usd=initial_capital_usd,
        score_threshold=score_threshold,
    )


def _iso_from_ms(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


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


def _decimal_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value, "f")


def _metrics_payload(metrics: BacktestMetrics) -> BacktestMetricsPayload:
    return BacktestMetricsPayload(
        run_id=metrics.run_id,
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        gross_pnl_usd=_decimal_str(metrics.gross_pnl) or "0",
        total_fees_usd=_decimal_str(metrics.total_fees) or "0",
        net_pnl_usd=_decimal_str(metrics.net_pnl) or "0",
        max_drawdown_usd=_decimal_str(metrics.max_drawdown) or "0",
        max_drawdown_pct=metrics.max_drawdown_pct,
        sharpe_ratio=metrics.sharpe_ratio,
        sortino_ratio=metrics.sortino_ratio,
        profit_factor=metrics.profit_factor,
        avg_hold_seconds=metrics.avg_hold_seconds,
        veto_count=metrics.veto_count,
    )


def _summary_from_run_row(row: dict[str, object]) -> BacktestRunSummaryItem:
    created_at = int(row["created_at"])
    start_ts = int(row["start_ts"])
    end_ts = int(row["end_ts"])
    initial_capital = row["initial_capital"]
    assert isinstance(initial_capital, Decimal)
    net_pnl = row.get("net_pnl")
    net_pnl_str: str | None = None
    if isinstance(net_pnl, Decimal):
        net_pnl_str = _decimal_str(net_pnl)
    win_rate_val = row.get("win_rate")
    win_rate_out: float | None = float(win_rate_val) if win_rate_val is not None else None
    total_trades_val = row.get("total_trades")
    total_trades_out: int | None = int(total_trades_val) if total_trades_val is not None else None
    veto_val = row.get("veto_count")
    veto_out: int | None = int(veto_val) if veto_val is not None else None
    return BacktestRunSummaryItem(
        run_id=str(row["run_id"]),
        created_at_iso=_iso_from_ms(created_at),
        asset=str(row["asset"]),
        timeframe=str(row["timeframe"]),
        start_ts_iso=_iso_from_ms(start_ts),
        end_ts_iso=_iso_from_ms(end_ts),
        initial_capital_usd=_decimal_str(initial_capital) or "0",
        net_pnl_usd=net_pnl_str,
        win_rate=win_rate_out,
        total_trades=total_trades_out,
        veto_count=veto_out,
    )


def _equity_points(result: BacktestResult) -> list[BacktestEquityPoint]:
    points: list[BacktestEquityPoint] = []
    for ts_ms, equity in result.equity_curve:
        points.append(
            BacktestEquityPoint(
                ts_iso=_iso_from_ms(ts_ms),
                equity_usd=_decimal_str(equity) or "0",
            ),
        )
    return points


def _equity_points_from_trades(
    initial_capital: Decimal,
    start_ts: int,
    trades: list[SimulatedTrade],
) -> list[BacktestEquityPoint]:
    """Rebuild a coarse equity rail from closed trade PnL when curve rows are not stored."""
    points: list[BacktestEquityPoint] = [
        BacktestEquityPoint(
            ts_iso=_iso_from_ms(start_ts),
            equity_usd=_decimal_str(initial_capital) or "0",
        ),
    ]
    closed = sorted(
        [
            trade
            for trade in trades
            if trade.exit_ts is not None and trade.net_pnl is not None
        ],
        key=lambda row: row.exit_ts or 0,
    )
    running = initial_capital
    for trade in closed:
        assert trade.exit_ts is not None
        assert trade.net_pnl is not None
        running = running + trade.net_pnl
        points.append(
            BacktestEquityPoint(
                ts_iso=_iso_from_ms(trade.exit_ts),
                equity_usd=_decimal_str(running) or "0",
            ),
        )
    return points


def _trade_rows(trades: list[SimulatedTrade]) -> list[BacktestTradeRow]:
    rows: list[BacktestTradeRow] = []
    for trade in trades:
        exit_iso: str | None = None
        if trade.exit_ts is not None:
            exit_iso = _iso_from_ms(trade.exit_ts)
        rows.append(
            BacktestTradeRow(
                trade_id=trade.trade_id,
                signal_id=trade.signal_id,
                asset=trade.asset,
                direction=trade.direction,
                entry_ts_iso=_iso_from_ms(trade.entry_ts),
                exit_ts_iso=exit_iso,
                entry_price=_decimal_str(trade.entry_price) or "0",
                exit_price=_decimal_str(trade.exit_price),
                net_pnl_usd=_decimal_str(trade.net_pnl),
                exit_reason=trade.exit_reason,
                risk_veto=trade.risk_veto,
            ),
        )
    return rows


async def open_dashboard_backtest_db(settings: PolarisSettings) -> BacktestDB:
    """Open DuckDB, ensure parent dirs and schema exist."""
    db_path = _resolve_db_path(settings)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = BacktestDB(db_path)
    await db.init_schema()
    return db


async def fetch_backtest_inventory(settings: PolarisSettings) -> BacktestInventoryPayload:
    db = await open_dashboard_backtest_db(settings)
    try:
        inv = await db.fetch_inventory()
        assets_raw = inv.get("assets", [])
        assets: list[str] = []
        if isinstance(assets_raw, list):
            assets = [str(item) for item in assets_raw]
        return BacktestInventoryPayload(
            db_path=str(_resolve_db_path(settings)),
            candle_count=int(inv.get("candle_count", 0)),
            signal_count=int(inv.get("signal_count", 0)),
            run_count=int(inv.get("run_count", 0)),
            assets=assets,
        )
    finally:
        db.close()


async def seed_demo_backtest_fixtures(settings: PolarisSettings) -> BacktestSeedPayload:
    root = _repo_root()
    candle_path = root / _SMOKE_CANDLES
    signal_path = root / _SMOKE_SIGNALS
    if not candle_path.is_file() or not signal_path.is_file():
        logger.error(
            "backtest_seed_missing | candles={} | signals={}",
            candle_path,
            signal_path,
        )
        return BacktestSeedPayload(
            candles_imported=0,
            signals_imported=0,
            asset=_SMOKE_ASSET,
            timeframe=_SMOKE_TIMEFRAME,
            message="smoke_fixtures_missing_on_disk",
        )

    db = await open_dashboard_backtest_db(settings)
    try:
        candle_count = await import_candles_from_csv(
            db,
            candle_path,
            _SMOKE_ASSET,
            _SMOKE_TIMEFRAME,
        )
        signal_rows = []
        for line in signal_path.read_bytes().splitlines():
            if not line.strip():
                continue
            signal_rows.append(signal_payload_to_row(line))
        await db.insert_signals(signal_rows)
        logger.info(
            "backtest_seed_complete | candles={} | signals={}",
            candle_count,
            len(signal_rows),
        )
        return BacktestSeedPayload(
            candles_imported=candle_count,
            signals_imported=len(signal_rows),
            asset=_SMOKE_ASSET,
            timeframe=_SMOKE_TIMEFRAME,
            message="demo_smoke_fixtures_loaded",
        )
    finally:
        db.close()


async def run_dashboard_backtest(
    settings: PolarisSettings,
    *,
    asset: str,
    timeframe: str,
    start_day: str,
    end_day: str,
    initial_capital_usd: Decimal,
    score_threshold: float,
) -> BacktestRunCreatedPayload:
    cfg = build_backtest_config(
        settings,
        initial_capital_usd=initial_capital_usd,
        score_threshold=score_threshold,
    )
    db = BacktestDB(cfg.db_path)
    await db.init_schema()
    try:
        engine = BacktestEngine(cfg, db)
        start_ts = _day_start_ms(start_day)
        end_ts = _day_end_ms(end_day)
        result = await engine.run(asset.upper(), timeframe, start_ts, end_ts)
        logger.info(
            "dashboard_backtest_complete | run_id={} | asset={} | trades={}",
            result.run_id,
            asset,
            result.metrics.total_trades,
        )
        return BacktestRunCreatedPayload(
            run_id=result.run_id,
            metrics=_metrics_payload(result.metrics),
            equity_curve=_equity_points(result),
            disclaimer=_DISCLAIMER,
        )
    finally:
        db.close()


async def list_dashboard_backtest_runs(
    settings: PolarisSettings,
    *,
    limit: int = 25,
) -> BacktestRunListPayload:
    db = await open_dashboard_backtest_db(settings)
    try:
        rows = await db.list_recent_runs(limit=limit)
        summaries = [_summary_from_run_row(row) for row in rows]
        return BacktestRunListPayload(runs=summaries)
    finally:
        db.close()


async def fetch_dashboard_backtest_run(
    settings: PolarisSettings,
    run_id: str,
) -> BacktestRunDetailPayload | None:
    db = await open_dashboard_backtest_db(settings)
    try:
        run_row = await db.fetch_run(run_id)
        metrics_row = await db.fetch_metrics(run_id)
        if run_row is None or metrics_row is None:
            return None
        trades = await db.fetch_trades(run_id)
        summary = _summary_from_run_row(
            {
                "run_id": run_row.run_id,
                "created_at": run_row.created_at,
                "asset": run_row.asset,
                "timeframe": run_row.timeframe,
                "start_ts": run_row.start_ts,
                "end_ts": run_row.end_ts,
                "initial_capital": run_row.initial_capital,
                "net_pnl": metrics_row.net_pnl,
                "win_rate": metrics_row.win_rate,
                "total_trades": metrics_row.total_trades,
                "veto_count": metrics_row.veto_count,
            },
        )
        equity_curve = _equity_points_from_trades(
            run_row.initial_capital,
            run_row.start_ts,
            trades,
        )
        return BacktestRunDetailPayload(
            run=summary,
            metrics=_metrics_payload(metrics_row),
            equity_curve=equity_curve,
            trades=_trade_rows(trades),
            disclaimer=_DISCLAIMER,
        )
    finally:
        db.close()
