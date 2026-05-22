"""DuckDB access for the offline backtester.

Every public coroutine delegates synchronous DuckDB work via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from prometheus.backtest.config import (
    BacktestConfig,
    BacktestMetrics,
    BacktestRun,
    CandleRow,
    SignalRow,
    SimulatedTrade,
)


_INIT_SQL = """
CREATE TABLE IF NOT EXISTS candles (
    asset       VARCHAR NOT NULL,
    timeframe   VARCHAR NOT NULL,
    ts          BIGINT  NOT NULL,
    open        DECIMAL(28, 10) NOT NULL,
    high        DECIMAL(28, 10) NOT NULL,
    low         DECIMAL(28, 10) NOT NULL,
    close       DECIMAL(28, 10) NOT NULL,
    volume      DECIMAL(28, 10) NOT NULL,
    PRIMARY KEY (asset, timeframe, ts)
);

CREATE TABLE IF NOT EXISTS bt_signals (
    signal_id       VARCHAR PRIMARY KEY,
    asset           VARCHAR NOT NULL,
    ts              BIGINT  NOT NULL,
    direction       VARCHAR NOT NULL,
    action          VARCHAR NOT NULL,
    total_score     DECIMAL(10, 4) NOT NULL,
    confidence      DECIMAL(10, 4) NOT NULL,
    risk_veto       BOOLEAN NOT NULL DEFAULT FALSE,
    ttl_seconds     INTEGER,
    raw_json        VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS bt_runs (
    run_id          VARCHAR PRIMARY KEY,
    created_at      BIGINT  NOT NULL,
    asset           VARCHAR NOT NULL,
    timeframe       VARCHAR NOT NULL,
    start_ts        BIGINT  NOT NULL,
    end_ts          BIGINT  NOT NULL,
    initial_capital DECIMAL(28, 10) NOT NULL,
    maker_fee_bps   DECIMAL(10, 4) NOT NULL,
    taker_fee_bps   DECIMAL(10, 4) NOT NULL,
    slippage_bps    DECIMAL(10, 4) NOT NULL,
    config_json     VARCHAR NOT NULL
);

CREATE TABLE IF NOT EXISTS bt_trades (
    trade_id        VARCHAR PRIMARY KEY,
    run_id          VARCHAR NOT NULL,
    signal_id       VARCHAR NOT NULL,
    asset           VARCHAR NOT NULL,
    direction       VARCHAR NOT NULL,
    entry_ts        BIGINT  NOT NULL,
    exit_ts         BIGINT,
    entry_price     DECIMAL(28, 10) NOT NULL,
    exit_price      DECIMAL(28, 10),
    size_base       DECIMAL(28, 10) NOT NULL,
    notional_usd    DECIMAL(28, 10) NOT NULL,
    stop_price      DECIMAL(28, 10),
    gross_pnl       DECIMAL(28, 10),
    fees_paid       DECIMAL(28, 10),
    net_pnl         DECIMAL(28, 10),
    exit_reason     VARCHAR,
    risk_veto       BOOLEAN NOT NULL DEFAULT FALSE,
    FOREIGN KEY (run_id) REFERENCES bt_runs(run_id)
);

CREATE TABLE IF NOT EXISTS bt_metrics (
    run_id              VARCHAR PRIMARY KEY,
    total_trades        INTEGER NOT NULL,
    winning_trades      INTEGER NOT NULL,
    losing_trades       INTEGER NOT NULL,
    win_rate            FLOAT NOT NULL,
    gross_pnl           DECIMAL(28, 10) NOT NULL,
    total_fees          DECIMAL(28, 10) NOT NULL,
    net_pnl             DECIMAL(28, 10) NOT NULL,
    max_drawdown        DECIMAL(28, 10) NOT NULL,
    max_drawdown_pct    FLOAT NOT NULL,
    sharpe_ratio        FLOAT,
    sortino_ratio       FLOAT,
    profit_factor       FLOAT,
    avg_win             DECIMAL(28, 10),
    avg_loss            DECIMAL(28, 10),
    largest_win         DECIMAL(28, 10),
    largest_loss        DECIMAL(28, 10),
    avg_hold_seconds    FLOAT,
    veto_count          INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES bt_runs(run_id)
);
"""

_TRADES_PL_SCHEMA = {
    "trade_id": pl.Utf8,
    "run_id": pl.Utf8,
    "signal_id": pl.Utf8,
    "asset": pl.Utf8,
    "direction": pl.Utf8,
    "entry_ts": pl.Int64,
    "exit_ts": pl.Int64,
    "entry_price": pl.Decimal(precision=28, scale=10),
    "exit_price": pl.Decimal(precision=28, scale=10),
    "size_base": pl.Decimal(precision=28, scale=10),
    "notional_usd": pl.Decimal(precision=28, scale=10),
    "stop_price": pl.Decimal(precision=28, scale=10),
    "gross_pnl": pl.Decimal(precision=28, scale=10),
    "fees_paid": pl.Decimal(precision=28, scale=10),
    "net_pnl": pl.Decimal(precision=28, scale=10),
    "exit_reason": pl.Utf8,
    "risk_veto": pl.Boolean,
}


def _connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection (sync)."""
    p = ":memory:" if str(path) == ":memory:" else str(path)
    return duckdb.connect(p)


def _init_schema_sync(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(_INIT_SQL)


def _insert_candles_sync(conn: duckdb.DuckDBPyConnection, rows: list[CandleRow]) -> None:
    if not rows:
        return
    df = pl.DataFrame(
        {
            "asset": [r.asset for r in rows],
            "timeframe": [r.timeframe for r in rows],
            "ts": [r.ts for r in rows],
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        },
        schema={
            "asset": pl.Utf8,
            "timeframe": pl.Utf8,
            "ts": pl.Int64,
            "open": pl.Decimal(precision=28, scale=10),
            "high": pl.Decimal(precision=28, scale=10),
            "low": pl.Decimal(precision=28, scale=10),
            "close": pl.Decimal(precision=28, scale=10),
            "volume": pl.Decimal(precision=28, scale=10),
        },
    )
    tbl = df.to_arrow()
    conn.register("_bt_candles_arrow", tbl)
    conn.execute(
        """
        INSERT INTO candles BY NAME
        SELECT * FROM _bt_candles_arrow
        """,
    )
    conn.unregister("_bt_candles_arrow")


def _insert_signals_sync(conn: duckdb.DuckDBPyConnection, rows: list[SignalRow]) -> None:
    if not rows:
        return
    df = pl.DataFrame(
        {
            "signal_id": [r.signal_id for r in rows],
            "asset": [r.asset for r in rows],
            "ts": [r.ts for r in rows],
            "direction": [r.direction for r in rows],
            "action": [r.action for r in rows],
            "total_score": [r.total_score for r in rows],
            "confidence": [r.confidence for r in rows],
            "risk_veto": [r.risk_veto for r in rows],
            "ttl_seconds": [r.ttl_seconds for r in rows],
            "raw_json": [r.raw_json for r in rows],
        },
        schema={
            "signal_id": pl.Utf8,
            "asset": pl.Utf8,
            "ts": pl.Int64,
            "direction": pl.Utf8,
            "action": pl.Utf8,
            "total_score": pl.Decimal(precision=10, scale=4),
            "confidence": pl.Decimal(precision=10, scale=4),
            "risk_veto": pl.Boolean,
            "ttl_seconds": pl.Int64,
            "raw_json": pl.Utf8,
        },
        infer_schema_length=None,
    )
    tbl = df.to_arrow()
    conn.register("_bt_signals_arrow", tbl)
    conn.execute(
        """
        INSERT INTO bt_signals BY NAME
        SELECT * FROM _bt_signals_arrow
        """,
    )
    conn.unregister("_bt_signals_arrow")


def _fetch_candles_sync(
    conn: duckdb.DuckDBPyConnection,
    asset: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
) -> list[CandleRow]:
    res = conn.execute(
        """
        SELECT asset, timeframe, ts, open, high, low, close, volume
        FROM candles
        WHERE asset = ? AND timeframe = ? AND ts >= ? AND ts <= ?
        ORDER BY ts ASC
        """,
        [asset, timeframe, start_ts, end_ts],
    ).fetchall()
    out: list[CandleRow] = []
    for row in res:
        out.append(
            CandleRow(
                asset=str(row[0]),
                timeframe=str(row[1]),
                ts=int(row[2]),
                open=Decimal(str(row[3])),
                high=Decimal(str(row[4])),
                low=Decimal(str(row[5])),
                close=Decimal(str(row[6])),
                volume=Decimal(str(row[7])),
            ),
        )
    return out


def _fetch_signals_sync(
    conn: duckdb.DuckDBPyConnection,
    asset: str,
    start_ts: int,
    end_ts: int,
) -> list[SignalRow]:
    res = conn.execute(
        """
        SELECT signal_id, asset, ts, direction, action,
               total_score, confidence, risk_veto, ttl_seconds, raw_json
        FROM bt_signals
        WHERE asset = ? AND ts >= ? AND ts <= ?
        ORDER BY ts ASC
        """,
        [asset, start_ts, end_ts],
    ).fetchall()
    out: list[SignalRow] = []
    for row in res:
        ttl_val = row[8]
        ttl_out: int | None = int(ttl_val) if ttl_val is not None else None
        out.append(
            SignalRow(
                signal_id=str(row[0]),
                asset=str(row[1]),
                ts=int(row[2]),
                direction=str(row[3]),
                action=str(row[4]),
                total_score=Decimal(str(row[5])),
                confidence=Decimal(str(row[6])),
                risk_veto=bool(row[7]),
                ttl_seconds=ttl_out,
                raw_json=str(row[9]),
            ),
        )
    return out


def _write_run_sync(conn: duckdb.DuckDBPyConnection, run: BacktestRun) -> None:
    conn.execute(
        """
        INSERT INTO bt_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            run.run_id,
            run.created_at,
            run.asset,
            run.timeframe,
            run.start_ts,
            run.end_ts,
            run.initial_capital,
            run.maker_fee_bps,
            run.taker_fee_bps,
            run.slippage_bps,
            run.config_json,
        ],
    )


def _trades_polars_frame(trades: list[SimulatedTrade]) -> pl.DataFrame:
    rows_dict: dict[str, Any] = {
        "trade_id": [t.trade_id for t in trades],
        "run_id": [t.run_id for t in trades],
        "signal_id": [t.signal_id for t in trades],
        "asset": [t.asset for t in trades],
        "direction": [t.direction for t in trades],
        "entry_ts": [t.entry_ts for t in trades],
        "exit_ts": [t.exit_ts for t in trades],
        "entry_price": [t.entry_price for t in trades],
        "exit_price": [t.exit_price for t in trades],
        "size_base": [t.size_base for t in trades],
        "notional_usd": [t.notional_usd for t in trades],
        "stop_price": [t.stop_price for t in trades],
        "gross_pnl": [t.gross_pnl for t in trades],
        "fees_paid": [t.fees_paid for t in trades],
        "net_pnl": [t.net_pnl for t in trades],
        "exit_reason": [t.exit_reason for t in trades],
        "risk_veto": [t.risk_veto for t in trades],
    }
    return pl.DataFrame(rows_dict, schema=_TRADES_PL_SCHEMA)


def _write_trades_sync(conn: duckdb.DuckDBPyConnection, trades: list[SimulatedTrade]) -> None:
    if not trades:
        return
    df = _trades_polars_frame(trades)
    tbl = df.to_arrow()
    conn.register("_bt_trades_arrow", tbl)
    conn.execute(
        """
        INSERT INTO bt_trades BY NAME
        SELECT * FROM _bt_trades_arrow
        """,
    )
    conn.unregister("_bt_trades_arrow")


def _fetch_trades_sync(conn: duckdb.DuckDBPyConnection, run_id: str) -> list[SimulatedTrade]:
    res = conn.execute(
        """
        SELECT trade_id, run_id, signal_id, asset, direction, entry_ts, exit_ts,
               entry_price, exit_price, size_base, notional_usd, stop_price,
               gross_pnl, fees_paid, net_pnl, exit_reason, risk_veto
        FROM bt_trades
        WHERE run_id = ?
        ORDER BY entry_ts ASC
        """,
        [run_id],
    ).fetchall()
    out: list[SimulatedTrade] = []
    for row in res:
        exit_ts_val = row[6]
        exit_ts_out: int | None = int(exit_ts_val) if exit_ts_val is not None else None
        stop_val = row[11]
        stop_out: Decimal | None = Decimal(str(stop_val)) if stop_val is not None else None
        exit_px_val = row[8]
        exit_px_out: Decimal | None = Decimal(str(exit_px_val)) if exit_px_val is not None else None
        gross_val = row[12]
        gross_out: Decimal | None = Decimal(str(gross_val)) if gross_val is not None else None
        fees_val = row[13]
        fees_out: Decimal | None = Decimal(str(fees_val)) if fees_val is not None else None
        net_val = row[14]
        net_out: Decimal | None = Decimal(str(net_val)) if net_val is not None else None
        exit_reason_val = row[15]
        exit_reason_out: str | None = str(exit_reason_val) if exit_reason_val is not None else None
        out.append(
            SimulatedTrade(
                trade_id=str(row[0]),
                run_id=str(row[1]),
                signal_id=str(row[2]),
                asset=str(row[3]),
                direction=str(row[4]),
                entry_ts=int(row[5]),
                exit_ts=exit_ts_out,
                entry_price=Decimal(str(row[7])),
                exit_price=exit_px_out,
                size_base=Decimal(str(row[9])),
                notional_usd=Decimal(str(row[10])),
                stop_price=stop_out,
                gross_pnl=gross_out,
                fees_paid=fees_out,
                net_pnl=net_out,
                exit_reason=exit_reason_out,
                risk_veto=bool(row[16]),
            ),
        )
    return out


def _count_inventory_sync(conn: duckdb.DuckDBPyConnection) -> dict[str, int | list[str]]:
    candle_row = conn.execute("SELECT COUNT(*) FROM candles").fetchone()
    signal_row = conn.execute("SELECT COUNT(*) FROM bt_signals").fetchone()
    run_row = conn.execute("SELECT COUNT(*) FROM bt_runs").fetchone()
    assets_rows = conn.execute(
        """
        SELECT DISTINCT asset FROM (
            SELECT asset FROM candles
            UNION
            SELECT asset FROM bt_signals
        ) ORDER BY asset
        """,
    ).fetchall()
    assets = [str(r[0]) for r in assets_rows]
    return {
        "candle_count": int(candle_row[0]) if candle_row else 0,
        "signal_count": int(signal_row[0]) if signal_row else 0,
        "run_count": int(run_row[0]) if run_row else 0,
        "assets": assets,
    }


def _list_recent_runs_sync(conn: duckdb.DuckDBPyConnection, limit: int) -> list[dict[str, object]]:
    res = conn.execute(
        """
        SELECT r.run_id, r.created_at, r.asset, r.timeframe, r.start_ts, r.end_ts,
               r.initial_capital, m.net_pnl, m.win_rate, m.total_trades, m.veto_count
        FROM bt_runs r
        LEFT JOIN bt_metrics m ON m.run_id = r.run_id
        ORDER BY r.created_at DESC
        LIMIT ?
        """,
        [limit],
    ).fetchall()
    out: list[dict[str, object]] = []
    for row in res:
        net_pnl_val = row[7]
        net_pnl_out: Decimal | None = Decimal(str(net_pnl_val)) if net_pnl_val is not None else None
        win_rate_val = row[8]
        win_rate_out: float | None = float(win_rate_val) if win_rate_val is not None else None
        total_trades_val = row[9]
        total_trades_out: int | None = int(total_trades_val) if total_trades_val is not None else None
        veto_val = row[10]
        veto_out: int | None = int(veto_val) if veto_val is not None else None
        out.append(
            {
                "run_id": str(row[0]),
                "created_at": int(row[1]),
                "asset": str(row[2]),
                "timeframe": str(row[3]),
                "start_ts": int(row[4]),
                "end_ts": int(row[5]),
                "initial_capital": Decimal(str(row[6])),
                "net_pnl": net_pnl_out,
                "win_rate": win_rate_out,
                "total_trades": total_trades_out,
                "veto_count": veto_out,
            },
        )
    return out


def _write_metrics_sync(conn: duckdb.DuckDBPyConnection, metrics: BacktestMetrics) -> None:
    conn.execute(
        """
        INSERT INTO bt_metrics VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            metrics.run_id,
            metrics.total_trades,
            metrics.winning_trades,
            metrics.losing_trades,
            metrics.win_rate,
            metrics.gross_pnl,
            metrics.total_fees,
            metrics.net_pnl,
            metrics.max_drawdown,
            metrics.max_drawdown_pct,
            metrics.sharpe_ratio,
            metrics.sortino_ratio,
            metrics.profit_factor,
            metrics.avg_win,
            metrics.avg_loss,
            metrics.largest_win,
            metrics.largest_loss,
            metrics.avg_hold_seconds,
            metrics.veto_count,
        ],
    )


class BacktestDB:
    """Single-connection DuckDB manager for one backtest session."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path: str | Path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    def _ensure_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = _connect(self._db_path)
        return self._conn

    async def init_schema(self) -> None:
        """Create tables if missing."""

        def _run() -> None:
            conn = self._ensure_conn()
            _init_schema_sync(conn)

        await asyncio.to_thread(_run)

    async def insert_candles(self, rows: list[CandleRow]) -> None:
        """Bulk-insert candles via Arrow."""

        def _run() -> None:
            conn = self._ensure_conn()
            _insert_candles_sync(conn, rows)

        await asyncio.to_thread(_run)

    async def insert_signals(self, rows: list[SignalRow]) -> None:
        """Bulk-insert replay signals via Arrow."""

        def _run() -> None:
            conn = self._ensure_conn()
            _insert_signals_sync(conn, rows)

        await asyncio.to_thread(_run)

    async def fetch_candles(
        self,
        asset: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
    ) -> list[CandleRow]:
        """Load candles in chronological order."""

        def _run() -> list[CandleRow]:
            conn = self._ensure_conn()
            return _fetch_candles_sync(conn, asset, timeframe, start_ts, end_ts)

        return await asyncio.to_thread(_run)

    async def fetch_signals(
        self,
        asset: str,
        start_ts: int,
        end_ts: int,
    ) -> list[SignalRow]:
        """Load signals for an asset in chronological order."""

        def _run() -> list[SignalRow]:
            conn = self._ensure_conn()
            return _fetch_signals_sync(conn, asset, start_ts, end_ts)

        return await asyncio.to_thread(_run)

    async def write_run(self, run: BacktestRun) -> None:
        """Persist run header row."""

        def _run() -> None:
            conn = self._ensure_conn()
            _write_run_sync(conn, run)

        await asyncio.to_thread(_run)

    async def write_trades(self, trades: list[SimulatedTrade]) -> None:
        """Persist simulated trades."""

        def _run() -> None:
            conn = self._ensure_conn()
            _write_trades_sync(conn, trades)

        await asyncio.to_thread(_run)

    async def write_metrics(self, metrics: BacktestMetrics) -> None:
        """Persist aggregate metrics."""

        def _run() -> None:
            conn = self._ensure_conn()
            _write_metrics_sync(conn, metrics)

        await asyncio.to_thread(_run)

    async def fetch_run(self, run_id: str) -> BacktestRun | None:
        """Load a single run record."""

        def _run() -> BacktestRun | None:
            conn = self._ensure_conn()
            row = conn.execute(
                """
                SELECT run_id, created_at, asset, timeframe, start_ts, end_ts,
                       initial_capital, maker_fee_bps, taker_fee_bps,
                       slippage_bps, config_json
                FROM bt_runs WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            if row is None:
                return None
            return BacktestRun(
                run_id=str(row[0]),
                created_at=int(row[1]),
                asset=str(row[2]),
                timeframe=str(row[3]),
                start_ts=int(row[4]),
                end_ts=int(row[5]),
                initial_capital=Decimal(str(row[6])),
                maker_fee_bps=Decimal(str(row[7])),
                taker_fee_bps=Decimal(str(row[8])),
                slippage_bps=Decimal(str(row[9])),
                config_json=str(row[10]),
            )

        return await asyncio.to_thread(_run)

    async def fetch_trades(self, run_id: str) -> list[SimulatedTrade]:
        """Load simulated trades for a completed run."""

        def _run() -> list[SimulatedTrade]:
            conn = self._ensure_conn()
            return _fetch_trades_sync(conn, run_id)

        return await asyncio.to_thread(_run)

    async def fetch_inventory(self) -> dict[str, int | list[str]]:
        """Row counts and distinct assets in the DuckDB warehouse."""

        def _run() -> dict[str, int | list[str]]:
            conn = self._ensure_conn()
            return _count_inventory_sync(conn)

        return await asyncio.to_thread(_run)

    async def list_recent_runs(self, limit: int = 25) -> list[dict[str, object]]:
        """Recent run headers joined with metrics when present."""

        def _run() -> list[dict[str, object]]:
            conn = self._ensure_conn()
            return _list_recent_runs_sync(conn, limit)

        return await asyncio.to_thread(_run)

    async def fetch_metrics(self, run_id: str) -> BacktestMetrics | None:
        """Load metrics for a run."""

        def _run() -> BacktestMetrics | None:
            conn = self._ensure_conn()
            row = conn.execute(
                """
                SELECT run_id, total_trades, winning_trades, losing_trades,
                       win_rate, gross_pnl, total_fees, net_pnl,
                       max_drawdown, max_drawdown_pct, sharpe_ratio,
                       sortino_ratio, profit_factor, avg_win, avg_loss,
                       largest_win, largest_loss, avg_hold_seconds, veto_count
                FROM bt_metrics WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
            if row is None:
                return None
            return BacktestMetrics(
                run_id=str(row[0]),
                total_trades=int(row[1]),
                winning_trades=int(row[2]),
                losing_trades=int(row[3]),
                win_rate=float(row[4]),
                gross_pnl=Decimal(str(row[5])),
                total_fees=Decimal(str(row[6])),
                net_pnl=Decimal(str(row[7])),
                max_drawdown=Decimal(str(row[8])),
                max_drawdown_pct=float(row[9]),
                sharpe_ratio=float(row[10]) if row[10] is not None else None,
                sortino_ratio=float(row[11]) if row[11] is not None else None,
                profit_factor=float(row[12]) if row[12] is not None else None,
                avg_win=Decimal(str(row[13])) if row[13] is not None else None,
                avg_loss=Decimal(str(row[14])) if row[14] is not None else None,
                largest_win=Decimal(str(row[15])) if row[15] is not None else None,
                largest_loss=Decimal(str(row[16])) if row[16] is not None else None,
                avg_hold_seconds=float(row[17]) if row[17] is not None else None,
                veto_count=int(row[18]),
            )

        return await asyncio.to_thread(_run)

    def close(self) -> None:
        """Close the DuckDB connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def open_backtest_db(config: BacktestConfig) -> BacktestDB:
    """Factory aligned with ``BacktestConfig.db_path``."""
    return BacktestDB(config.db_path)
