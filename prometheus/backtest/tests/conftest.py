"""Shared fixtures for PROMETHEUS DuckDB backtests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from decimal import Decimal

import pytest

from prometheus.backtest.config import BacktestConfig, CandleRow, SignalRow
from prometheus.backtest.db import BacktestDB


@pytest.fixture
def backtest_db() -> Iterator[BacktestDB]:
    db = BacktestDB(":memory:")

    async def _init() -> None:
        await db.init_schema()

    asyncio.run(_init())
    yield db
    db.close()


@pytest.fixture
def sample_candles() -> list[CandleRow]:
    base = 1_700_000_000_000
    step = 3_600_000
    rows: list[CandleRow] = []
    for i in range(100):
        px = Decimal("40000") + (Decimal("5000") * Decimal(i) / Decimal("99"))
        rows.append(
            CandleRow(
                asset="BTCUSDT",
                timeframe="1h",
                ts=base + i * step,
                open=px,
                high=px + Decimal("10"),
                low=px - Decimal("10"),
                close=px,
                volume=Decimal("100"),
            ),
        )
    return rows


@pytest.fixture
def sample_signals(sample_candles: list[CandleRow]) -> list[SignalRow]:
    base_ts = sample_candles[0].ts
    gap = 3_600_000
    out: list[SignalRow] = []
    for i in range(20):
        direction = "LONG" if i % 3 == 0 else "SHORT" if i % 3 == 1 else "NO_POSITION"
        veto = i % 7 == 0
        score = Decimal("80") if i % 2 == 0 else Decimal("40")
        conf = Decimal("0.60") if i % 2 == 0 else Decimal("0.40")
        action = "OPEN" if direction != "NO_POSITION" else "HOLD"
        out.append(
            SignalRow(
                signal_id="sig_{}".format(i),
                asset="BTCUSDT",
                ts=base_ts + i * gap,
                direction=direction,
                action=action,
                total_score=score,
                confidence=conf,
                risk_veto=veto,
                ttl_seconds=3600 if i % 5 == 0 else None,
                raw_json="{}",
            ),
        )
    return out


@pytest.fixture
def default_config() -> BacktestConfig:
    return BacktestConfig(
        initial_capital_usd=Decimal("100000"),
        risk_per_trade_pct=Decimal("0.01"),
        max_open_positions=3,
        maker_fee_bps=Decimal("2"),
        taker_fee_bps=Decimal("6"),
        slippage_bps=Decimal("5"),
        default_stop_distance_pct=Decimal("0.02"),
        score_threshold=65.0,
        min_confidence=0.55,
    )
