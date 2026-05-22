"""Tests for ``BacktestEngine``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from prometheus.backtest.config import BacktestConfig, CandleRow, SignalRow
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.engine import BacktestEngine


def _make_uptrend_candles() -> list[CandleRow]:
    base = 1_800_000_000_000
    rows: list[CandleRow] = []
    for i in range(10):
        px = Decimal("100") + Decimal(i)
        rows.append(
            CandleRow(
                asset="BTCUSDT",
                timeframe="1h",
                ts=base + i * 3_600_000,
                open=px,
                high=px + Decimal("1"),
                low=px - Decimal("1"),
                close=px,
                volume=Decimal("1"),
            ),
        )
    return rows


@pytest.mark.asyncio
async def test_empty_signal_set_produces_no_trades(
    backtest_db: BacktestDB,
    sample_candles: list[CandleRow],
    default_config: BacktestConfig,
) -> None:
    await backtest_db.insert_candles(sample_candles)
    eng = BacktestEngine(default_config, backtest_db)
    c0, c1 = sample_candles[0], sample_candles[-1]
    res = await eng.run("BTCUSDT", "1h", c0.ts, c1.ts)
    assert res.metrics.total_trades == 0


@pytest.mark.asyncio
async def test_all_vetoed_signals_produce_no_trades(
    backtest_db: BacktestDB,
    sample_candles: list[CandleRow],
    default_config: BacktestConfig,
) -> None:
    await backtest_db.insert_candles(sample_candles)
    sigs = []
    for i in range(5):
        sigs.append(
            SignalRow(
                signal_id="v{}".format(i),
                asset="BTCUSDT",
                ts=sample_candles[i].ts,
                direction="LONG",
                action="OPEN",
                total_score=Decimal("90"),
                confidence=Decimal("0.90"),
                risk_veto=True,
                ttl_seconds=None,
                raw_json="{}",
            ),
        )
    await backtest_db.insert_signals(sigs)
    eng = BacktestEngine(default_config, backtest_db)
    c0, c1 = sample_candles[0], sample_candles[-1]
    res = await eng.run("BTCUSDT", "1h", c0.ts, c1.ts)
    assert res.metrics.total_trades == 0
    assert res.veto_count >= 5


@pytest.mark.asyncio
async def test_position_limit_prevents_excess_entries(
    backtest_db: BacktestDB,
    sample_candles: list[CandleRow],
    default_config: BacktestConfig,
) -> None:
    cfg = default_config.model_copy(update={"max_open_positions": 1})
    await backtest_db.insert_candles(sample_candles)
    t0 = sample_candles[0].ts
    t1 = sample_candles[3].ts
    sigs = [
        SignalRow(
            signal_id="a",
            asset="BTCUSDT",
            ts=t0,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.80"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
        SignalRow(
            signal_id="b",
            asset="BTCUSDT",
            ts=t1,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.80"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
    ]
    await backtest_db.insert_signals(sigs)
    eng = BacktestEngine(cfg, backtest_db)
    c0, c1 = sample_candles[0], sample_candles[-1]
    res = await eng.run("BTCUSDT", "1h", c0.ts, c1.ts)
    assert len(res.trades) == 1


@pytest.mark.asyncio
async def test_capital_updated_after_trade_close(
    backtest_db: BacktestDB,
    default_config: BacktestConfig,
) -> None:
    cans = _make_uptrend_candles()
    await backtest_db.insert_candles(cans)
    sig_open = SignalRow(
        signal_id="open1",
        asset="BTCUSDT",
        ts=cans[0].ts,
        direction="LONG",
        action="OPEN",
        total_score=Decimal("80"),
        confidence=Decimal("0.80"),
        risk_veto=False,
        ttl_seconds=None,
        raw_json="{}",
    )
    await backtest_db.insert_signals([sig_open])
    eng = BacktestEngine(default_config, backtest_db)
    res = await eng.run("BTCUSDT", "1h", cans[0].ts, cans[-1].ts)
    assert res.metrics.total_trades == 1
    assert res.metrics.net_pnl is not None


@pytest.mark.asyncio
async def test_end_of_data_force_closes_open_positions(
    backtest_db: BacktestDB,
    default_config: BacktestConfig,
) -> None:
    cans = _make_uptrend_candles()
    await backtest_db.insert_candles(cans)
    sig_open = SignalRow(
        signal_id="open1",
        asset="BTCUSDT",
        ts=cans[0].ts,
        direction="LONG",
        action="OPEN",
        total_score=Decimal("80"),
        confidence=Decimal("0.80"),
        risk_veto=False,
        ttl_seconds=None,
        raw_json="{}",
    )
    await backtest_db.insert_signals([sig_open])
    eng = BacktestEngine(default_config, backtest_db)
    res = await eng.run("BTCUSDT", "1h", cans[0].ts, cans[-1].ts)
    tr = res.trades[0]
    assert tr.exit_reason == "END_OF_DATA"
    assert tr.exit_ts is not None


@pytest.mark.asyncio
async def test_equity_curve_length_equals_candle_count(
    backtest_db: BacktestDB,
    sample_candles: list[CandleRow],
    default_config: BacktestConfig,
) -> None:
    await backtest_db.insert_candles(sample_candles)
    eng = BacktestEngine(default_config, backtest_db)
    c0, c1 = sample_candles[0], sample_candles[-1]
    res = await eng.run("BTCUSDT", "1h", c0.ts, c1.ts)
    assert len(res.equity_curve) == len(sample_candles)


@pytest.mark.asyncio
async def test_signal_flip_closes_existing_and_opens_new(
    backtest_db: BacktestDB,
    default_config: BacktestConfig,
) -> None:
    cans = _make_uptrend_candles()
    await backtest_db.insert_candles(cans)
    sigs = [
        SignalRow(
            signal_id="L",
            asset="BTCUSDT",
            ts=cans[0].ts,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.80"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
        SignalRow(
            signal_id="S",
            asset="BTCUSDT",
            ts=cans[5].ts,
            direction="SHORT",
            action="OPEN",
            total_score=Decimal("80"),
            confidence=Decimal("0.80"),
            risk_veto=False,
            ttl_seconds=None,
            raw_json="{}",
        ),
    ]
    await backtest_db.insert_signals(sigs)
    eng = BacktestEngine(default_config, backtest_db)
    res = await eng.run("BTCUSDT", "1h", cans[0].ts, cans[-1].ts)
    flip = [t for t in res.trades if t.exit_reason == "SIGNAL_FLIP"]
    assert len(flip) == 1
