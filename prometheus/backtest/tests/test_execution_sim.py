"""Tests for ``execution_sim``."""

from __future__ import annotations

from decimal import Decimal

import pytest

from prometheus.backtest.config import BacktestConfig, CandleRow, SignalRow, SimulatedTrade
from prometheus.backtest.execution_sim import (
    simulate_entry,
    simulate_exit,
    usd_notional_to_base_coin_size,
)


def _candle(ts: int, close: str, low: str | None = None, high: str | None = None) -> CandleRow:
    px = Decimal(close)
    lo = Decimal(low) if low is not None else px
    hi = Decimal(high) if high is not None else px
    return CandleRow(
        asset="BTCUSDT",
        timeframe="1h",
        ts=ts,
        open=px,
        high=hi,
        low=lo,
        close=px,
        volume=Decimal("1"),
    )


def _sig_open_long(**kw: object) -> SignalRow:
    data: dict[str, object] = {
        "signal_id": "s1",
        "asset": "BTCUSDT",
        "ts": 1,
        "direction": "LONG",
        "action": "OPEN",
        "total_score": Decimal("80"),
        "confidence": Decimal("0.80"),
        "risk_veto": False,
        "ttl_seconds": None,
        "raw_json": "{}",
    }
    data.update(kw)
    return SignalRow.model_validate(data)


@pytest.mark.asyncio
async def test_veto_signal_returns_none() -> None:
    cfg = BacktestConfig()
    sig = _sig_open_long(risk_veto=True)
    out = await simulate_entry(sig, _candle(1, "40000"), Decimal("100000"), cfg)
    assert out is None


@pytest.mark.asyncio
async def test_score_below_threshold_returns_none() -> None:
    cfg = BacktestConfig(score_threshold=65.0)
    sig = _sig_open_long(total_score=Decimal("40"))
    out = await simulate_entry(sig, _candle(1, "40000"), Decimal("100000"), cfg)
    assert out is None


@pytest.mark.asyncio
async def test_long_entry_applies_slippage_correctly() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("100"))
    can = _candle(1, "100")
    sig = _sig_open_long()
    tr = await simulate_entry(sig, can, Decimal("100000"), cfg)
    assert tr is not None
    assert tr.entry_price > can.close


@pytest.mark.asyncio
async def test_short_entry_applies_slippage_correctly() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("100"))
    can = _candle(1, "100")
    sig = _sig_open_long(direction="SHORT")
    tr = await simulate_entry(sig, can, Decimal("100000"), cfg)
    assert tr is not None
    assert tr.entry_price < can.close


@pytest.mark.asyncio
async def test_stop_loss_exit_long() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("0"))
    can_entry = _candle(1, "100")
    sig = _sig_open_long()
    tr_open = await simulate_entry(sig, can_entry, Decimal("100000"), cfg)
    assert tr_open is not None
    assert tr_open.stop_price is not None
    stop_px = tr_open.stop_price
    tr = SimulatedTrade(
        trade_id="t",
        run_id="r",
        signal_id="s",
        asset="BTCUSDT",
        direction="LONG",
        entry_ts=1000,
        exit_ts=None,
        entry_price=tr_open.entry_price,
        exit_price=None,
        size_base=tr_open.size_base,
        notional_usd=tr_open.notional_usd,
        stop_price=stop_px,
        gross_pnl=None,
        fees_paid=None,
        net_pnl=None,
        exit_reason=None,
        risk_veto=False,
        ttl_seconds=None,
    )
    pierce = _candle(2000, "99", low=str(stop_px - Decimal("1")))
    closed = await simulate_exit(tr, [pierce], None, cfg)
    assert closed.exit_reason == "STOP_LOSS"


@pytest.mark.asyncio
async def test_stop_loss_exit_short() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("0"))
    can_entry = _candle(1, "100")
    sig = _sig_open_long(direction="SHORT")
    tr_open = await simulate_entry(sig, can_entry, Decimal("100000"), cfg)
    assert tr_open is not None
    stop_px = tr_open.stop_price
    assert stop_px is not None
    tr = SimulatedTrade(
        trade_id="t",
        run_id="r",
        signal_id="s",
        asset="BTCUSDT",
        direction="SHORT",
        entry_ts=1000,
        exit_ts=None,
        entry_price=tr_open.entry_price,
        exit_price=None,
        size_base=tr_open.size_base,
        notional_usd=tr_open.notional_usd,
        stop_price=stop_px,
        gross_pnl=None,
        fees_paid=None,
        net_pnl=None,
        exit_reason=None,
        risk_veto=False,
        ttl_seconds=None,
    )
    pierce = _candle(2000, "101", high=str(stop_px + Decimal("1")))
    closed = await simulate_exit(tr, [pierce], None, cfg)
    assert closed.exit_reason == "STOP_LOSS"


@pytest.mark.asyncio
async def test_ttl_expiry_exit() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("0"))
    can_entry = _candle(1000, "100")
    sig = _sig_open_long(ttl_seconds=1)
    tr_open = await simulate_entry(sig, can_entry, Decimal("100000"), cfg)
    assert tr_open is not None
    tr = SimulatedTrade(
        trade_id="t",
        run_id="r",
        signal_id="s",
        asset="BTCUSDT",
        direction="LONG",
        entry_ts=1000,
        exit_ts=None,
        entry_price=tr_open.entry_price,
        exit_price=None,
        size_base=tr_open.size_base,
        notional_usd=tr_open.notional_usd,
        stop_price=tr_open.stop_price,
        gross_pnl=None,
        fees_paid=None,
        net_pnl=None,
        exit_reason=None,
        risk_veto=False,
        ttl_seconds=1,
    )
    late = _candle(4000, "100")
    closed = await simulate_exit(tr, [late], None, cfg)
    assert closed.exit_reason == "EXPIRY"


@pytest.mark.asyncio
async def test_risk_usd_formula_no_leverage_term() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("0"))
    capital = Decimal("100000")
    can = _candle(1, "50000")
    sig = _sig_open_long()
    tr = await simulate_entry(sig, can, capital, cfg)
    assert tr is not None
    risk_budget = capital * cfg.risk_per_trade_pct
    risk_from_trade = tr.notional_usd * cfg.default_stop_distance_pct
    cap_notional = capital * Decimal("0.20")
    raw_notional = risk_budget / cfg.default_stop_distance_pct
    expected_notional = min(raw_notional, cap_notional)
    assert abs(tr.notional_usd - expected_notional) < Decimal("0.01")
    expected_risk = expected_notional * cfg.default_stop_distance_pct
    assert abs(risk_from_trade - expected_risk) < Decimal("0.01")
    assert risk_from_trade <= risk_budget + Decimal("0.01")


@pytest.mark.asyncio
async def test_size_base_is_base_coin_not_usd() -> None:
    cfg = BacktestConfig(slippage_bps=Decimal("0"))
    can = _candle(1, "25000")
    sig = _sig_open_long()
    tr = await simulate_entry(sig, can, Decimal("100000"), cfg)
    assert tr is not None
    approx = usd_notional_to_base_coin_size(tr.notional_usd, tr.entry_price)
    assert abs(tr.size_base - approx) < Decimal("0.0000001")
