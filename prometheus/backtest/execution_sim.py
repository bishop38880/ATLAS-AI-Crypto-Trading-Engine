"""Simulated fills — mirrors PROMETHEUS sizing/stops without exchange I/O.

Exit trigger order (deterministic):
    1) Stop-loss intra-bar (low/high versus stop_price)
    2) TTL expiry versus candle timestamp (exit at that candle close)
    3) Explicit closing_signal at or before candle evaluation
    4) End of candle stream — close at last candle

risk_usd [USD] = notional_usd [USD] × stop_distance_pct [dimensionless].
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from loguru import logger

from prometheus.backtest.config import BacktestConfig, CandleRow, SignalRow, SimulatedTrade

BPS_SCALE = Decimal("10000")


def usd_notional_to_base_coin_size(notional_usd: Decimal, price: Decimal) -> Decimal:
    """Convert USD notional to base-coin units (offline quantisation)."""
    if price <= 0:
        raise ValueError("price must be positive for sizing")
    return notional_usd / price


def _should_skip_entry(signal: SignalRow, config: BacktestConfig) -> bool:
    """Return ``True`` when no opening trade should be simulated."""
    if signal.risk_veto:
        logger.info(
            "entry_skipped_risk_veto | asset={} | signal_id={}",
            signal.asset,
            signal.signal_id,
        )
        return True
    if signal.direction == "NO_POSITION":
        return True
    threshold = Decimal(str(config.score_threshold))
    if signal.total_score < threshold:
        return True
    min_conf = Decimal(str(config.min_confidence))
    if signal.confidence < min_conf:
        return True
    return False


def _resolve_entry_notional(
    capital_available: Decimal,
    stop_pct: Decimal,
    config: BacktestConfig,
) -> Decimal | None:
    """Risk-sized notional capped at 20% of capital."""
    if stop_pct <= 0:
        return None
    risk_budget = capital_available * config.risk_per_trade_pct
    cap_notional = capital_available * Decimal("0.20")
    raw_notional = risk_budget / stop_pct
    notional_usd = min(raw_notional, cap_notional)
    return notional_usd if notional_usd > 0 else None


def _pending_open_trade(
    signal: SignalRow,
    candle_at_signal: CandleRow,
    config: BacktestConfig,
    notional_usd: Decimal,
    stop_pct: Decimal,
) -> SimulatedTrade:
    slip = config.slippage_bps / BPS_SCALE
    raw_close = candle_at_signal.close
    entry_price = apply_entry_slippage(signal.direction, raw_close, slip)
    stop_price = compute_stop_price(entry_price, signal.direction, stop_pct)
    size_base = usd_notional_to_base_coin_size(notional_usd, entry_price)
    risk_usd = notional_usd * stop_pct
    _log_risk_sanity(notional_usd, stop_pct, risk_usd)
    return SimulatedTrade(
        trade_id="__pending__",
        run_id="__pending__",
        signal_id=signal.signal_id,
        asset=signal.asset,
        direction=signal.direction,
        entry_ts=candle_at_signal.ts,
        exit_ts=None,
        entry_price=entry_price,
        exit_price=None,
        size_base=size_base,
        notional_usd=notional_usd,
        stop_price=stop_price,
        gross_pnl=None,
        fees_paid=None,
        net_pnl=None,
        exit_reason=None,
        risk_veto=False,
        ttl_seconds=signal.ttl_seconds,
    )


async def simulate_entry(
    signal: SignalRow,
    candle_at_signal: CandleRow,
    capital_available: Decimal,
    config: BacktestConfig,
) -> SimulatedTrade | None:
    """Return an opening ``SimulatedTrade`` or ``None`` if gated."""
    if _should_skip_entry(signal, config):
        return None
    stop_pct = config.default_stop_distance_pct
    notional_usd = _resolve_entry_notional(capital_available, stop_pct, config)
    if notional_usd is None:
        return None
    return _pending_open_trade(signal, candle_at_signal, config, notional_usd, stop_pct)


def apply_entry_slippage(direction: str, close_px: Decimal, slip_frac: Decimal) -> Decimal:
    """Apply adverse entry slippage from midpoint close."""
    if direction == "LONG":
        return close_px * (Decimal("1") + slip_frac)
    if direction == "SHORT":
        return close_px * (Decimal("1") - slip_frac)
    raise ValueError("unsupported direction for entry")


def apply_exit_slippage(direction: str, raw_px: Decimal, slip_frac: Decimal) -> Decimal:
    """Apply adverse exit slippage."""
    if direction == "LONG":
        return raw_px * (Decimal("1") - slip_frac)
    if direction == "SHORT":
        return raw_px * (Decimal("1") + slip_frac)
    raise ValueError("unsupported direction for exit")


def compute_stop_price(entry: Decimal, direction: str, stop_pct: Decimal) -> Decimal:
    """Fixed-percent stop from slipped entry."""
    if direction == "LONG":
        return entry * (Decimal("1") - stop_pct)
    if direction == "SHORT":
        return entry * (Decimal("1") + stop_pct)
    raise ValueError("unsupported direction for stop")


def _log_risk_sanity(notional: Decimal, stop_pct: Decimal, risk_usd: Decimal) -> None:
    expected = notional * stop_pct
    if expected != risk_usd:
        logger.warning(
            "risk_usd_mismatch | expected={} | got={}",
            expected,
            risk_usd,
        )


def resolve_ttl_cutoff_ms(trade: SimulatedTrade) -> int | None:
    """Exclusive TTL cutoff in unix ms."""
    if trade.ttl_seconds is None:
        return None
    return trade.entry_ts + int(trade.ttl_seconds) * 1000


def _try_exit_on_bar(
    trade: SimulatedTrade,
    candle: CandleRow,
    entry_fee: Decimal,
    maker_frac: Decimal,
    slip: Decimal,
    ttl_cutoff: int | None,
    closing_signal: SignalRow | None,
) -> SimulatedTrade | None:
    stopped = stop_hit(trade, candle)
    if stopped is not None:
        return finalize_exit(
            trade,
            stopped,
            candle.ts,
            entry_fee,
            maker_frac,
            slip,
            "STOP_LOSS",
        )
    if ttl_cutoff is not None and candle.ts > ttl_cutoff:
        return finalize_exit(
            trade,
            candle.close,
            candle.ts,
            entry_fee,
            maker_frac,
            slip,
            "EXPIRY",
        )
    sig_exit = signal_based_exit(trade, closing_signal, candle.ts)
    if sig_exit is not None:
        return finalize_exit(
            trade,
            candle.close,
            candle.ts,
            entry_fee,
            maker_frac,
            slip,
            sig_exit,
        )
    return None


async def simulate_exit(
    trade: SimulatedTrade,
    candles_since_entry: list[CandleRow],
    closing_signal: SignalRow | None,
    config: BacktestConfig,
) -> SimulatedTrade:
    """Walk candles until an exit trigger fires."""

    def _sync_walk() -> SimulatedTrade:
        slip = config.slippage_bps / BPS_SCALE
        maker_frac = config.maker_fee_bps / BPS_SCALE
        taker_frac = config.taker_fee_bps / BPS_SCALE
        entry_fee = trade.notional_usd * taker_frac
        ttl_cutoff = resolve_ttl_cutoff_ms(trade)
        for candle in candles_since_entry:
            done = _try_exit_on_bar(
                trade,
                candle,
                entry_fee,
                maker_frac,
                slip,
                ttl_cutoff,
                closing_signal,
            )
            if done is not None:
                return done
        if not candles_since_entry:
            raise ValueError("no candles for exit simulation")
        last = candles_since_entry[-1]
        return finalize_exit(
            trade,
            last.close,
            last.ts,
            entry_fee,
            maker_frac,
            slip,
            "END_OF_DATA",
        )

    return await asyncio.to_thread(_sync_walk)


def stop_hit(trade: SimulatedTrade, candle: CandleRow) -> Decimal | None:
    """Return stop price if hit this bar; else ``None``."""
    sp = trade.stop_price
    if sp is None:
        return None
    if trade.direction == "LONG" and candle.low <= sp:
        return sp
    if trade.direction == "SHORT" and candle.high >= sp:
        return sp
    return None


def signal_based_exit(
    trade: SimulatedTrade,
    closing_signal: SignalRow | None,
    candle_ts: int,
) -> str | None:
    """Return exit reason if signal mandates exit at ``candle_ts``."""
    if closing_signal is None:
        return None
    if closing_signal.ts > candle_ts:
        return None
    if closing_signal.action == "CLOSE":
        return "SIGNAL_CLOSE"
    if closing_signal.action == "OPEN" and closing_signal.direction != trade.direction:
        return "SIGNAL_FLIP"
    return None


def finalize_exit(
    trade: SimulatedTrade,
    raw_exit_px: Decimal,
    exit_ts: int,
    entry_fee: Decimal,
    maker_frac: Decimal,
    slip_frac: Decimal,
    reason: str,
) -> SimulatedTrade:
    """Attach exit fields and PnL."""
    exit_fill = apply_exit_slippage(trade.direction, raw_exit_px, slip_frac)
    exit_notional = exit_fill * trade.size_base
    exit_fee = exit_notional * maker_frac

    if trade.direction == "LONG":
        gross = (exit_fill - trade.entry_price) * trade.size_base
    elif trade.direction == "SHORT":
        gross = (trade.entry_price - exit_fill) * trade.size_base
    else:
        raise ValueError("unsupported trade direction")

    fees = entry_fee + exit_fee
    net = gross - fees
    return trade.model_copy(
        update={
            "exit_ts": exit_ts,
            "exit_price": exit_fill,
            "gross_pnl": gross,
            "fees_paid": fees,
            "net_pnl": net,
            "exit_reason": reason,
        },
    )


def close_trade_at_bar(
    trade: SimulatedTrade,
    raw_exit_px: Decimal,
    exit_ts: int,
    reason: str,
    config: BacktestConfig,
) -> SimulatedTrade:
    """Synchronous exit helper for replay engine bar-by-bar."""
    slip = config.slippage_bps / BPS_SCALE
    maker_frac = config.maker_fee_bps / BPS_SCALE
    taker_frac = config.taker_fee_bps / BPS_SCALE
    entry_fee = trade.notional_usd * taker_frac
    return finalize_exit(trade, raw_exit_px, exit_ts, entry_fee, maker_frac, slip, reason)
