"""Time-ordered replay of candles and recorded signals — offline only."""

from __future__ import annotations

import time
import uuid
from bisect import bisect_left
from decimal import Decimal
from typing import Literal

from loguru import logger

from prometheus.backtest.config import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestRun,
    CandleRow,
    SignalRow,
    SimulatedTrade,
    backtest_config_json,
)
from prometheus.backtest.db import BacktestDB
from prometheus.backtest.execution_sim import close_trade_at_bar, simulate_entry, stop_hit
from prometheus.backtest.loader import load_candles_window, load_signals_window
from prometheus.backtest.metrics import compute_metrics


EventKind = Literal["candle", "signal"]


class BacktestEngine:
    """Replay DuckDB candles + signals through the execution simulator."""

    def __init__(self, config: BacktestConfig, db: BacktestDB) -> None:
        self._cfg = config
        self._db = db

    async def run(
        self,
        asset: str,
        timeframe: str,
        start_ts: int,
        end_ts: int,
    ) -> BacktestResult:
        candles = await load_candles_window(self._db, asset, timeframe, start_ts, end_ts)
        signals = await load_signals_window(self._db, asset, start_ts, end_ts)
        run_id = str(uuid.uuid4())
        st = _ReplayState(
            cfg=self._cfg,
            run_id=run_id,
            asset=asset,
            candles=candles,
            capital=self._cfg.initial_capital_usd,
        )
        events = _merge_events(candles, signals)
        for _evt_ts, kind, payload in events:
            if kind == "candle":
                assert isinstance(payload, CandleRow)
                _on_candle(st, payload)
            else:
                assert isinstance(payload, SignalRow)
                await _process_signal_event(st, payload)
        _force_close_all(st)
        metrics = compute_metrics(
            run_id,
            st.closed_trades,
            st.equity_curve,
            self._cfg.initial_capital_usd,
            st.veto_count,
        )
        run_row = _make_run_row(self._cfg, run_id, asset, timeframe, start_ts, end_ts)
        await self._db.write_run(run_row)
        await self._db.write_trades(st.closed_trades)
        await self._db.write_metrics(metrics)
        return BacktestResult(
            run_id=run_id,
            metrics=metrics,
            trades=st.closed_trades,
            equity_curve=st.equity_curve,
            veto_count=st.veto_count,
        )


class _ReplayState:
    """Mutable replay accumulator (not a Pydantic model)."""

    def __init__(
        self,
        cfg: BacktestConfig,
        run_id: str,
        asset: str,
        candles: list[CandleRow],
        capital: Decimal,
    ) -> None:
        self.cfg = cfg
        self.run_id = run_id
        self.asset = asset
        self.candles = candles
        self.capital = capital
        self.open_positions: dict[str, SimulatedTrade] = {}
        self.closed_trades: list[SimulatedTrade] = []
        self.equity_curve: list[tuple[int, Decimal]] = []
        self.veto_count = 0
        self.last_candle: CandleRow | None = None


def _merge_events(
    candles: list[CandleRow],
    signals: list[SignalRow],
) -> list[tuple[int, EventKind, CandleRow | SignalRow]]:
    events: list[tuple[int, int, EventKind, CandleRow | SignalRow]] = []
    for c in candles:
        events.append((c.ts, 0, "candle", c))
    for s in signals:
        events.append((s.ts, 1, "signal", s))
    events.sort(key=lambda x: (x[0], x[1]))
    return [(ts, k, p) for ts, _, k, p in events]


def _on_candle(st: _ReplayState, candle: CandleRow) -> None:
    st.last_candle = candle
    _check_stop_losses(st)
    _expire_ttl_positions(st, candle)
    _update_equity_curve(st, candle.ts)


async def _process_signal_event(st: _ReplayState, signal: SignalRow) -> None:
    if st.last_candle is not None:
        _check_stop_losses(st)
    if signal.risk_veto:
        st.veto_count += 1
        logger.info(
            "signal_veto_no_position | asset={} | signal_id={}",
            signal.asset,
            signal.signal_id,
        )
        return
    candle_at = _first_candle_at_or_after(st.candles, signal.ts)
    if candle_at is None:
        return
    if signal.action == "CLOSE":
        _close_asset_positions(st, candle_at, signal, "SIGNAL_CLOSE")
        return
    if signal.action == "OPEN" and signal.direction in ("LONG", "SHORT"):
        await _handle_open_signal(st, signal, candle_at)


async def _handle_open_signal(
    st: _ReplayState,
    signal: SignalRow,
    candle_at: CandleRow,
) -> None:
    opp = [t for t in st.open_positions.values() if t.asset == signal.asset]
    conflict = [t for t in opp if t.direction != signal.direction]
    for tr in conflict:
        _close_position(st, tr.trade_id, candle_at.close, candle_at.ts, "SIGNAL_FLIP")
    if len(st.open_positions) >= st.cfg.max_open_positions:
        logger.warning(
            "entry_skipped_max_positions | asset={} | signal_id={}",
            signal.asset,
            signal.signal_id,
        )
        return
    raw = await simulate_entry(signal, candle_at, st.capital, st.cfg)
    if raw is None:
        return
    tid = str(uuid.uuid4())
    opened = raw.model_copy(update={"trade_id": tid, "run_id": st.run_id})
    st.open_positions[tid] = opened


def _close_asset_positions(
    st: _ReplayState,
    candle_at: CandleRow,
    signal: SignalRow,
    reason: str,
) -> None:
    to_close = [t for t in st.open_positions.values() if t.asset == signal.asset]
    for tr in to_close:
        _close_position(st, tr.trade_id, candle_at.close, candle_at.ts, reason)


def _check_stop_losses(st: _ReplayState) -> None:
    c = st.last_candle
    if c is None:
        return
    hits: list[tuple[str, Decimal]] = []
    for tid, tr in st.open_positions.items():
        px = stop_hit(tr, c)
        if px is not None:
            hits.append((tid, px))
    for tid, px in hits:
        _close_position(st, tid, px, c.ts, "STOP_LOSS")


def _expire_ttl_positions(st: _ReplayState, candle: CandleRow) -> None:
    expired_ids: list[str] = []
    for tid, tr in st.open_positions.items():
        if tr.ttl_seconds is None:
            continue
        cutoff = tr.entry_ts + int(tr.ttl_seconds) * 1000
        if candle.ts > cutoff:
            expired_ids.append(tid)
    for tid in expired_ids:
        _close_position(st, tid, candle.close, candle.ts, "EXPIRY")


def _close_position(
    st: _ReplayState,
    trade_id: str,
    raw_exit_px: Decimal,
    exit_ts: int,
    reason: str,
) -> None:
    tr = st.open_positions.pop(trade_id)
    closed = close_trade_at_bar(tr, raw_exit_px, exit_ts, reason, st.cfg)
    _apply_closed_trade(st, closed)


def _apply_closed_trade(st: _ReplayState, trade: SimulatedTrade) -> None:
    net = trade.net_pnl if trade.net_pnl is not None else Decimal("0")
    new_cap = st.capital + net
    if new_cap < Decimal("0"):
        logger.critical(
            "capital_would_go_negative | clamping | proposed={}",
            new_cap,
        )
        st.capital = Decimal("0")
    else:
        st.capital = new_cap
    st.closed_trades.append(trade)


def _update_equity_curve(st: _ReplayState, ts: int) -> None:
    mark = _mark_equity(st)
    st.equity_curve.append((ts, mark))


def _mark_equity(st: _ReplayState) -> Decimal:
    c = st.last_candle
    if c is None:
        return st.capital
    unreal = Decimal("0")
    for tr in st.open_positions.values():
        close_px = c.close
        if tr.direction == "LONG":
            unreal += (close_px - tr.entry_price) * tr.size_base
        elif tr.direction == "SHORT":
            unreal += (tr.entry_price - close_px) * tr.size_base
    return st.capital + unreal


def _force_close_all(st: _ReplayState) -> None:
    if not st.candles:
        return
    last = st.candles[-1]
    st.last_candle = last
    ids = list(st.open_positions.keys())
    for tid in ids:
        _close_position(st, tid, last.close, last.ts, "END_OF_DATA")
    if st.equity_curve:
        ts_tail = st.equity_curve[-1][0]
        st.equity_curve[-1] = (ts_tail, st.capital)


def _first_candle_at_or_after(candles: list[CandleRow], ts: int) -> CandleRow | None:
    ts_list = [c.ts for c in candles]
    i = bisect_left(ts_list, ts)
    return candles[i] if i < len(candles) else None


def _make_run_row(
    cfg: BacktestConfig,
    run_id: str,
    asset: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
) -> BacktestRun:
    return BacktestRun(
        run_id=run_id,
        created_at=int(time.time() * 1000),
        asset=asset,
        timeframe=timeframe,
        start_ts=start_ts,
        end_ts=end_ts,
        initial_capital=cfg.initial_capital_usd,
        maker_fee_bps=cfg.maker_fee_bps,
        taker_fee_bps=cfg.taker_fee_bps,
        slippage_bps=cfg.slippage_bps,
        config_json=backtest_config_json(cfg),
    )
