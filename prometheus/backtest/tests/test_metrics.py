"""Tests for ``metrics``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from prometheus.backtest.config import BacktestConfig, CandleRow, SignalRow, SimulatedTrade
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.engine import BacktestEngine
from prometheus.backtest.metrics import compute_metrics


def _closed_trade(net: str, gid: str = "g") -> SimulatedTrade:
    return SimulatedTrade(
        trade_id="t",
        run_id="r",
        signal_id=gid,
        asset="BTCUSDT",
        direction="LONG",
        entry_ts=1,
        exit_ts=2,
        entry_price=Decimal("100"),
        exit_price=Decimal("101"),
        size_base=Decimal("1"),
        notional_usd=Decimal("100"),
        stop_price=Decimal("99"),
        gross_pnl=Decimal(net),
        fees_paid=Decimal("0"),
        net_pnl=Decimal(net),
        exit_reason="END_OF_DATA",
        risk_veto=False,
    )


def test_win_rate_zero_when_no_trades() -> None:
    m = compute_metrics("run", [], [], Decimal("10000"), 0)
    assert m.win_rate == 0.0
    assert m.total_trades == 0


def test_win_rate_correct_for_mixed_trades() -> None:
    trades = [_closed_trade("10"), _closed_trade("-5")]
    curve = [(1, Decimal("10000")), (2, Decimal("10005")), (3, Decimal("10010"))]
    m = compute_metrics("run", trades, curve, Decimal("10000"), 0)
    assert m.total_trades == 2
    assert abs(m.win_rate - 0.5) < 1e-9


def test_max_drawdown_detected_correctly() -> None:
    curve = [
        (1, Decimal("100")),
        (2, Decimal("120")),
        (3, Decimal("90")),
    ]
    m = compute_metrics("run", [], curve, Decimal("100"), 0)
    assert m.max_drawdown == Decimal("30")


def test_sharpe_returns_none_for_single_trade() -> None:
    curve = [(1, Decimal("100")), (2, Decimal("110"))]
    m = compute_metrics("run", [_closed_trade("5")], curve, Decimal("100"), 0)
    assert m.sharpe_ratio is None


def test_profit_factor_returns_none_when_no_losses() -> None:
    trades = [_closed_trade("5"), _closed_trade("3")]
    curve = [(1, Decimal("100")), (2, Decimal("110"))]
    m = compute_metrics("run", trades, curve, Decimal("100"), 0)
    assert m.profit_factor is None


@pytest.mark.asyncio
async def test_veto_count_matches_engine_skips(
    backtest_db: BacktestDB,
    sample_candles: list[CandleRow],
    default_config: BacktestConfig,
) -> None:
    await backtest_db.insert_candles(sample_candles)
    veto_sigs = [
        SignalRow(
            signal_id="veto_{}".format(i),
            asset="BTCUSDT",
            ts=sample_candles[0].ts + i * 3_600_000,
            direction="LONG",
            action="OPEN",
            total_score=Decimal("90"),
            confidence=Decimal("0.9"),
            risk_veto=True,
            ttl_seconds=None,
            raw_json="{}",
        )
        for i in range(3)
    ]
    await backtest_db.insert_signals(veto_sigs)
    res = await BacktestEngine(default_config, backtest_db).run(
        "BTCUSDT",
        "1h",
        sample_candles[0].ts,
        sample_candles[-1].ts,
    )
    assert res.veto_count == 3
    assert res.metrics.total_trades == 0


def test_sortino_returns_none_when_no_negative_returns() -> None:
    curve = []
    day = 86_400_000
    for i in range(5):
        curve.append((i * day, Decimal(str(100 + i))))
    trades = [_closed_trade("1")]
    m = compute_metrics("run", trades, curve, Decimal("100"), 0)
    assert m.sortino_ratio is None
