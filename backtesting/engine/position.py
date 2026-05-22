"""Position tracking and P&L calculation during replay."""

from __future__ import annotations

import uuid
from decimal import Decimal

from pydantic import BaseModel

from backtesting.data.models import OHLCVBar
from backtesting.engine.config import RiskConfig
from backtesting.engine.scorer import BarScore


class TradeEntry(BaseModel, frozen=True):
    """Recorded trade entry."""

    trade_id: str
    asset: str
    direction: str
    entry_bar_index: int
    entry_timestamp_utc: str
    entry_price: Decimal
    entry_price_with_slippage: Decimal
    position_size_usd: Decimal
    leverage: int
    stop_loss: Decimal
    take_profit: Decimal
    score_at_entry: int
    derivatives_score: int = 0
    technical_score: int = 0
    market_context_score: int = 0
    signal_class: str


class TradeExit(BaseModel, frozen=True):
    """Recorded trade exit."""

    trade_id: str
    exit_bar_index: int
    exit_timestamp_utc: str
    exit_price: Decimal
    exit_price_with_slippage: Decimal
    exit_reason: str
    pnl_usd: Decimal
    pnl_pct: Decimal
    duration_bars: int


class ClosedTrade(BaseModel, frozen=True):
    """Complete round-trip trade."""

    entry: TradeEntry
    exit: TradeExit


class _OpenPosition(BaseModel, frozen=True):
    entry: TradeEntry
    entry_score: int


class PositionTracker:
    """Manages open positions during replay and enforces risk rules."""

    def __init__(self, asset: str, config: RiskConfig) -> None:
        self._asset = asset
        self._config = config
        self._open_positions: dict[str, _OpenPosition] = {}
        self._closed_trades: list[ClosedTrade] = []
        self._max_concurrent = int(
            config.max_portfolio_exposure_pct / config.max_position_pct,
        )

    def try_open(
        self,
        score: BarScore,
        bar: OHLCVBar,
        account_value: Decimal,
        config: RiskConfig,
        bar_index: int,
    ) -> TradeEntry | None:
        """Attempt to open a position when risk rules allow."""
        if score.signal_class == "NO_TRADE" or score.direction == "NEUTRAL":
            return None
        if len(self._open_positions) >= self._max_concurrent:
            return None
        if self.current_exposure_pct(account_value) >= config.max_portfolio_exposure_pct:
            return None
        leverage = _leverage_for_signal(score.signal_class, config.max_leverage)
        position_size = _calculate_position_size(account_value, config, score.atr, bar.close)
        if position_size <= Decimal("0"):
            return None
        entry = _build_trade_entry(
            asset=self._asset,
            score=score,
            bar=bar,
            bar_index=bar_index,
            position_size=position_size,
            leverage=leverage,
            config=config,
        )
        self._open_positions[entry.trade_id] = _OpenPosition(
            entry=entry,
            entry_score=score.total,
        )
        return entry

    def update(
        self,
        bar: OHLCVBar,
        current_bar_index: int,
        score: BarScore | None = None,
        thresholds_weak: int = 120,
        max_hold_bars: int = 96,
    ) -> list[ClosedTrade]:
        """Close positions that hit stop, take profit, decay, or max hold."""
        closed_now: list[ClosedTrade] = []
        for trade_id in list(self._open_positions.keys()):
            position = self._open_positions[trade_id]
            exit_reason = _detect_exit_reason(
                position.entry,
                bar,
                current_bar_index,
                score,
                thresholds_weak,
                max_hold_bars,
            )
            if exit_reason is None:
                continue
            closed_trade = _close_position(
                position,
                bar,
                current_bar_index,
                exit_reason,
                self._config,
            )
            del self._open_positions[trade_id]
            self._closed_trades.append(closed_trade)
            closed_now.append(closed_trade)
        return closed_now

    def force_close_all(
        self,
        bar: OHLCVBar,
        bar_index: int,
        reason: str = "BACKTEST_END",
    ) -> list[ClosedTrade]:
        """Force-close every open position at the current bar."""
        closed_now: list[ClosedTrade] = []
        for trade_id in list(self._open_positions.keys()):
            position = self._open_positions.pop(trade_id)
            closed_trade = _close_position(position, bar, bar_index, reason, self._config)
            self._closed_trades.append(closed_trade)
            closed_now.append(closed_trade)
        return closed_now

    def current_exposure_pct(self, account_value: Decimal) -> Decimal:
        """Total open notional as a percentage of account value."""
        if account_value <= Decimal("0"):
            return Decimal("0")
        total = sum(position.entry.position_size_usd for position in self._open_positions.values())
        return total / account_value * Decimal("100")

    def open_count(self) -> int:
        """Number of currently open positions."""
        return len(self._open_positions)

    @property
    def closed_trades(self) -> list[ClosedTrade]:
        """All trades closed so far."""
        return list(self._closed_trades)


def _leverage_for_signal(signal_class: str, max_leverage: int) -> int:
    mapping = {"STRONG": 10, "BUY": 7, "WEAK": 3}
    leverage = mapping.get(signal_class, 1)
    return min(leverage, max_leverage)


def _calculate_position_size(
    account_value: Decimal,
    config: RiskConfig,
    atr: Decimal,
    price: Decimal,
) -> Decimal:
    max_by_pct = account_value * config.max_position_pct / Decimal("100")
    if atr <= Decimal("0") or price <= Decimal("0"):
        return max_by_pct
    stop_distance = atr * config.stop_loss_atr_multiple / price
    if stop_distance <= Decimal("0"):
        return max_by_pct
    risk_budget = account_value * config.risk_per_trade_pct / Decimal("100")
    risk_based = risk_budget / stop_distance
    return min(max_by_pct, risk_based)


def _build_trade_entry(
    asset: str,
    score: BarScore,
    bar: OHLCVBar,
    bar_index: int,
    position_size: Decimal,
    leverage: int,
    config: RiskConfig,
) -> TradeEntry:
    slippage_factor = Decimal(config.slippage_bps) / Decimal("10000")
    if score.direction == "LONG":
        entry_with_slippage = bar.close * (Decimal("1") + slippage_factor)
        stop_loss = bar.close - score.atr * config.stop_loss_atr_multiple
        take_profit = bar.close + score.atr * config.take_profit_atr_multiple
    else:
        entry_with_slippage = bar.close * (Decimal("1") - slippage_factor)
        stop_loss = bar.close + score.atr * config.stop_loss_atr_multiple
        take_profit = bar.close - score.atr * config.take_profit_atr_multiple
    return TradeEntry(
        trade_id=str(uuid.uuid4())[:8],
        asset=asset,
        direction=score.direction,
        entry_bar_index=bar_index,
        entry_timestamp_utc=bar.timestamp_utc,
        entry_price=bar.close,
        entry_price_with_slippage=entry_with_slippage,
        position_size_usd=position_size,
        leverage=leverage,
        stop_loss=stop_loss,
        take_profit=take_profit,
        score_at_entry=score.total,
        derivatives_score=score.derivatives,
        technical_score=score.technical,
        market_context_score=score.market_context,
        signal_class=score.signal_class,
    )


def _detect_exit_reason(
    entry: TradeEntry,
    bar: OHLCVBar,
    bar_index: int,
    score: BarScore | None,
    thresholds_weak: int,
    max_hold_bars: int,
) -> str | None:
    if entry.direction == "LONG":
        if bar.low <= entry.stop_loss:
            return "STOP_LOSS"
        if bar.high >= entry.take_profit:
            return "TAKE_PROFIT"
    else:
        if bar.high >= entry.stop_loss:
            return "STOP_LOSS"
        if bar.low <= entry.take_profit:
            return "TAKE_PROFIT"
    duration = bar_index - entry.entry_bar_index
    if duration >= max_hold_bars:
        return "MAX_HOLD_BARS"
    if score is not None and score.total < thresholds_weak:
        return "SIGNAL_DECAY"
    return None


def _close_position(
    position: _OpenPosition,
    bar: OHLCVBar,
    bar_index: int,
    reason: str,
    config: RiskConfig,
) -> ClosedTrade:
    entry = position.entry
    exit_price = _exit_price_for_reason(entry, bar, reason)
    slippage_factor = Decimal(config.slippage_bps) / Decimal("10000")
    if entry.direction == "LONG":
        exit_with_slippage = exit_price * (Decimal("1") - slippage_factor)
        raw_pnl_pct = (exit_with_slippage - entry.entry_price_with_slippage) / entry.entry_price_with_slippage
    else:
        exit_with_slippage = exit_price * (Decimal("1") + slippage_factor)
        raw_pnl_pct = (entry.entry_price_with_slippage - exit_with_slippage) / entry.entry_price_with_slippage
    fee_factor = Decimal(config.fee_bps) / Decimal("10000") * Decimal("2")
    pnl_pct = raw_pnl_pct - fee_factor
    pnl_usd = pnl_pct * entry.position_size_usd
    trade_exit = TradeExit(
        trade_id=entry.trade_id,
        exit_bar_index=bar_index,
        exit_timestamp_utc=bar.timestamp_utc,
        exit_price=exit_price,
        exit_price_with_slippage=exit_with_slippage,
        exit_reason=reason,
        pnl_usd=pnl_usd,
        pnl_pct=pnl_pct * Decimal("100"),
        duration_bars=bar_index - entry.entry_bar_index,
    )
    return ClosedTrade(entry=entry, exit=trade_exit)


def _exit_price_for_reason(entry: TradeEntry, bar: OHLCVBar, reason: str) -> Decimal:
    if reason == "STOP_LOSS":
        return entry.stop_loss
    if reason == "TAKE_PROFIT":
        return entry.take_profit
    return bar.close
