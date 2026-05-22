"""DuckDB schema and read/write helpers for the backtesting suite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from backtesting.data.constants import DEFAULT_DB_FILENAME
from backtesting.data.models import DataCoverage, FundingRateBar, OHLCVBar

_SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS ohlcv (
    asset        VARCHAR NOT NULL,
    timestamp_utc TIMESTAMP NOT NULL,
    timeframe    VARCHAR NOT NULL,
    open         DECIMAL(20, 8) NOT NULL,
    high         DECIMAL(20, 8) NOT NULL,
    low          DECIMAL(20, 8) NOT NULL,
    close        DECIMAL(20, 8) NOT NULL,
    volume       DECIMAL(20, 8) NOT NULL,
    volume_usd   DECIMAL(20, 8) NOT NULL,
    PRIMARY KEY (asset, timeframe, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS funding_rates (
    asset              VARCHAR NOT NULL,
    timestamp_utc      TIMESTAMP NOT NULL,
    funding_rate       DECIMAL(20, 10) NOT NULL,
    funding_annualised DECIMAL(20, 6) NOT NULL,
    open_interest_usd  DECIMAL(20, 2),
    PRIMARY KEY (asset, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS liquidations (
    asset         VARCHAR NOT NULL,
    timestamp_utc TIMESTAMP NOT NULL,
    timeframe     VARCHAR NOT NULL,
    long_liq_usd  DECIMAL(20, 2) NOT NULL DEFAULT 0,
    short_liq_usd DECIMAL(20, 2) NOT NULL DEFAULT 0,
    PRIMARY KEY (asset, timeframe, timestamp_utc)
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id       VARCHAR PRIMARY KEY,
    created_at   TIMESTAMP NOT NULL,
    config_json  VARCHAR NOT NULL,
    status       VARCHAR NOT NULL,
    summary_json VARCHAR
);
"""

_db_singleton: BacktestDB | None = None


class BacktestDB:
    """DuckDB connection wrapper for local backtesting analytics."""

    def __init__(self, db_path: Path | None = None) -> None:
        default_path = Path(__file__).resolve().parent / DEFAULT_DB_FILENAME
        self._db_path = db_path or default_path
        self._connection = duckdb.connect(str(self._db_path))
        self._connection.execute(_SCHEMA_SQL)

    @classmethod
    def instance(cls, db_path: Path | None = None) -> BacktestDB:
        """Return the module-level singleton connection."""
        global _db_singleton
        if _db_singleton is None or db_path is not None:
            _db_singleton = cls(db_path=db_path)
        return _db_singleton

    def close(self) -> None:
        """Close the DuckDB connection."""
        self._connection.close()

    def write_ohlcv(self, bars: list[OHLCVBar]) -> int:
        """Bulk insert OHLCV bars using INSERT OR IGNORE."""
        if not bars:
            return 0
        rows = [
            (
                bar.asset,
                bar.timestamp_utc,
                bar.timeframe,
                str(bar.open),
                str(bar.high),
                str(bar.low),
                str(bar.close),
                str(bar.volume),
                str(bar.volume_usd),
            )
            for bar in bars
        ]
        before = self._connection.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        self._connection.executemany(
            """
            INSERT OR IGNORE INTO ohlcv
            (asset, timestamp_utc, timeframe, open, high, low, close, volume, volume_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        after = self._connection.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        return int(after - before)

    def write_funding_rates(self, rates: list[FundingRateBar]) -> int:
        """Bulk insert funding rate bars."""
        if not rates:
            return 0
        rows = [
            (
                rate.asset,
                rate.timestamp_utc,
                str(rate.funding_rate),
                str(rate.funding_rate_annualised),
                str(rate.open_interest_usd) if rate.open_interest_usd is not None else None,
            )
            for rate in rates
        ]
        before = self._connection.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
        self._connection.executemany(
            """
            INSERT OR IGNORE INTO funding_rates
            (asset, timestamp_utc, funding_rate, funding_annualised, open_interest_usd)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )
        after = self._connection.execute("SELECT COUNT(*) FROM funding_rates").fetchone()[0]
        return int(after - before)

    def get_ohlcv(
        self,
        asset: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> pl.DataFrame:
        """Return ascending OHLCV rows as a Polars DataFrame."""
        relation = self._connection.execute(
            """
            SELECT timestamp_utc, open, high, low, close, volume, volume_usd
            FROM ohlcv
            WHERE asset = ? AND timeframe = ?
              AND timestamp_utc >= ? AND timestamp_utc <= ?
            ORDER BY timestamp_utc ASC
            """,
            [asset, timeframe, start, end],
        )
        return pl.from_arrow(relation.arrow())

    def get_funding_rates(self, asset: str, start: str, end: str) -> pl.DataFrame:
        """Return funding rates as a Polars DataFrame."""
        relation = self._connection.execute(
            """
            SELECT timestamp_utc, funding_rate, funding_annualised, open_interest_usd
            FROM funding_rates
            WHERE asset = ? AND timestamp_utc >= ? AND timestamp_utc <= ?
            ORDER BY timestamp_utc ASC
            """,
            [asset, start, end],
        )
        return pl.from_arrow(relation.arrow())

    def get_coverage(self, asset: str | None = None) -> list[DataCoverage]:
        """Return coverage summaries for one asset or the full database."""
        assets = _resolve_coverage_assets(self._connection, asset)
        return [_build_coverage_row(self._connection, symbol) for symbol in assets]

    def list_recent_runs(self, limit: int = 10) -> list[dict[str, object]]:
        """Return recent backtest run summaries newest first."""
        rows = self._connection.execute(
            """
            SELECT run_id, created_at, config_json, summary_json
            FROM backtest_runs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [
            {
                "run_id": str(row[0]),
                "created_at": row[1],
                "config_json": str(row[2]),
                "summary_json": str(row[3]) if row[3] is not None else None,
            }
            for row in rows
        ]

    def get_run_record(self, run_id: str) -> dict[str, object] | None:
        """Fetch a single backtest run row by ID."""
        row = self._connection.execute(
            """
            SELECT run_id, created_at, config_json, summary_json, status
            FROM backtest_runs WHERE run_id = ?
            """,
            [run_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "run_id": str(row[0]),
            "created_at": row[1],
            "config_json": str(row[2]),
            "summary_json": str(row[3]) if row[3] is not None else None,
            "status": str(row[4]),
        }

    def get_assets_with_full_coverage(
        self,
        start: str,
        end: str,
        timeframe: str = "1h",
        require_funding: bool = True,
    ) -> list[str]:
        """Return assets with complete OHLCV (+ funding) for the date range."""
        query = """
            SELECT asset
            FROM ohlcv
            WHERE timeframe = ?
              AND timestamp_utc >= ?
              AND timestamp_utc <= ?
            GROUP BY asset
            HAVING COUNT(*) > 0
        """
        rows = self._connection.execute(query, [timeframe, start, end]).fetchall()
        candidates = [str(row[0]) for row in rows]
        if not require_funding:
            return candidates
        return [
            symbol
            for symbol in candidates
            if _has_funding_in_range(self._connection, symbol, start, end)
        ]


def _resolve_coverage_assets(connection: duckdb.DuckDBPyConnection, asset: str | None) -> list[str]:
    if asset is not None:
        return [asset]
    rows = connection.execute(
        """
        SELECT DISTINCT asset FROM ohlcv
        UNION
        SELECT DISTINCT asset FROM funding_rates
        ORDER BY 1
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _build_coverage_row(
    connection: duckdb.DuckDBPyConnection,
    asset: str,
) -> DataCoverage:
    ohlcv_stats = connection.execute(
        """
        SELECT MIN(timestamp_utc), MAX(timestamp_utc), COUNT(*)
        FROM ohlcv WHERE asset = ?
        """,
        [asset],
    ).fetchone()
    funding_stats = connection.execute(
        """
        SELECT MIN(timestamp_utc), MAX(timestamp_utc), COUNT(*)
        FROM funding_rates WHERE asset = ?
        """,
        [asset],
    ).fetchone()
    timeframe_rows = connection.execute(
        "SELECT DISTINCT timeframe FROM ohlcv WHERE asset = ? ORDER BY 1",
        [asset],
    ).fetchall()
    liq_count = connection.execute(
        "SELECT COUNT(*) FROM liquidations WHERE asset = ?",
        [asset],
    ).fetchone()[0]
    return DataCoverage(
        asset=asset,
        ohlcv_start=_format_timestamp(ohlcv_stats[0]),
        ohlcv_end=_format_timestamp(ohlcv_stats[1]),
        ohlcv_bar_count=int(ohlcv_stats[2] or 0),
        funding_start=_format_timestamp(funding_stats[0]),
        funding_end=_format_timestamp(funding_stats[1]),
        funding_bar_count=int(funding_stats[2] or 0),
        liq_bar_count=int(liq_count or 0),
        timeframes_available=[str(row[0]) for row in timeframe_rows],
    )


def _has_funding_in_range(
    connection: duckdb.DuckDBPyConnection,
    asset: str,
    start: str,
    end: str,
) -> bool:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM funding_rates
        WHERE asset = ? AND timestamp_utc >= ? AND timestamp_utc <= ?
        """,
        [asset, start, end],
    ).fetchone()
    return int(row[0] or 0) > 0


def _format_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
