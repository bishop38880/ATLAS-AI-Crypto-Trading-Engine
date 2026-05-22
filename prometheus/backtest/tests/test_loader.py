"""Tests for DuckDB loaders."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from prometheus.backtest.config import CandleRow, SignalRow
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.loader import (
    import_candles_from_csv,
    load_candles_window,
    load_signals_window,
    signal_payload_to_row,
)


@pytest.mark.asyncio
async def test_candles_loaded_in_ts_order(backtest_db: BacktestDB) -> None:
    rows = [
        CandleRow(
            asset="BTCUSDT",
            timeframe="1h",
            ts=300,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
        CandleRow(
            asset="BTCUSDT",
            timeframe="1h",
            ts=100,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=Decimal("1"),
        ),
    ]
    await backtest_db.insert_candles(rows)
    out = await load_candles_window(backtest_db, "BTCUSDT", "1h", 50, 400)
    assert [r.ts for r in out] == [100, 300]


@pytest.mark.asyncio
async def test_signals_filtered_to_time_range(backtest_db: BacktestDB) -> None:
    sigs = [
        SignalRow(
            signal_id="a",
            asset="BTCUSDT",
            ts=100,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.8"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
        SignalRow(
            signal_id="b",
            asset="BTCUSDT",
            ts=500,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.8"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
    ]
    await backtest_db.insert_signals(sigs)
    out = await load_signals_window(backtest_db, "BTCUSDT", 200, 600)
    assert len(out) == 1
    assert out[0].signal_id == "b"


@pytest.mark.asyncio
async def test_bulk_insert_candles_via_arrow(backtest_db: BacktestDB) -> None:
    batch = [
        CandleRow(
            asset="ETHUSDT",
            timeframe="15m",
            ts=1000 + i,
            open=Decimal("2000"),
            high=Decimal("2001"),
            low=Decimal("1999"),
            close=Decimal("2000"),
            volume=Decimal("10"),
        )
        for i in range(50)
    ]
    await backtest_db.insert_candles(batch)
    got = await backtest_db.fetch_candles("ETHUSDT", "15m", 1000, 1100)
    assert len(got) == 50


@pytest.mark.asyncio
async def test_csv_import_parses_decimal_correctly(tmp_path: Path, backtest_db: BacktestDB) -> None:
    p = tmp_path / "c.csv"
    lines = ["ts,open,high,low,close,volume", "1000,1.5,2.5,0.5,2.0,123.456"]
    p.write_text("\n".join(lines), encoding="utf-8")
    n = await import_candles_from_csv(backtest_db, p, "BTCUSDT", "1m")
    assert n == 1
    rows = await backtest_db.fetch_candles("BTCUSDT", "1m", 500, 2000)
    assert rows[0].close == Decimal("2.0")


def test_signal_payload_missing_timestamp_raises() -> None:
    with pytest.raises(ValueError, match="timestamp"):
        signal_payload_to_row(b'{"signal_id":"x","asset":"BTCUSDT"}')
