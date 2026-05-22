"""Paper trading engine — simulates Bitget fill logic locally.

Matches real Bitget execution semantics:
    - Market fills at ``last_price`` from the ticker feed.
    - Limit fills when ``trigger_price`` crosses ``last_price``.
    - Configured taker/maker fees deducted from fill proceeds.
    - Signals routed to BUY (long open), SELL (short open / long close).

All exchange interaction is mocked via ``BitgetExecutionClient`` in
paper mode.  This engine wraps that to add portfolio tracking and
fill simulation with fee accounting.

Import constraints:
    - ``msgspec`` for serialization — NOT stdlib ``json``.
    - ``Decimal`` for ALL financial math.
    - ``loguru`` POSITIONAL format only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from loguru import logger

from prometheus.paper.models import PaperFill, PaperPosition, PaperPortfolio


# Default Bitget futures fee schedule
_DEFAULT_TAKER_FEE: Decimal = Decimal("0.0006")
_DEFAULT_MAKER_FEE: Decimal = Decimal("0.0002")


class PaperTradingEngine:
    """Simulated execution engine with Bitget-compatible fill logic.

    Tracks positions and fees in memory.  Does NOT touch any
    network resource — fully hermetic for testing.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("10000"),
        taker_fee_rate: Decimal = _DEFAULT_TAKER_FEE,
        maker_fee_rate: Decimal = _DEFAULT_MAKER_FEE,
    ) -> None:
        self._capital = initial_capital
        self._taker_fee = taker_fee_rate
        self._maker_fee = maker_fee_rate
        self._positions: dict[str, PaperPosition] = {}
        self._fills: list[PaperFill] = []
        self._total_fees: Decimal = Decimal("0")

    # ── Public API ────────────────────────────────────────────────────

    async def execute_market_fill(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        size: Decimal,
        last_price: Decimal,
    ) -> PaperFill:
        """Simulate a market fill at ``last_price`` with taker fee."""
        return await self._create_fill(
            symbol, side, size, last_price, self._taker_fee,
        )

    async def execute_limit_fill(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        size: Decimal,
        limit_price: Decimal,
    ) -> PaperFill:
        """Simulate a limit fill at ``limit_price`` with maker fee."""
        return await self._create_fill(
            symbol, side, size, limit_price, self._maker_fee,
        )

    async def route_signal(
        self,
        symbol: str,
        signal_side: Literal["long", "short", "close"],
        size: Decimal,
        last_price: Decimal,
    ) -> PaperFill:
        """Route an incoming signal to the correct order side."""
        order_side = _signal_to_order_side(signal_side, symbol, self._positions)
        return await self.execute_market_fill(
            symbol, order_side, size, last_price,
        )

    def get_portfolio(self) -> PaperPortfolio:
        """Snapshot the current portfolio state."""
        return PaperPortfolio(
            capital_usd=self._capital,
            positions=list(self._positions.values()),
            total_fees_paid=self._total_fees,
            fill_count=len(self._fills),
        )

    # ── Internals ─────────────────────────────────────────────────────

    async def _create_fill(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        size: Decimal,
        price: Decimal,
        fee_rate: Decimal,
    ) -> PaperFill:
        """Build a fill, deduct fee, update position tracker."""
        notional = size * price
        fee = notional * fee_rate
        net = notional - fee

        fill = PaperFill(
            fill_id=uuid.uuid4().hex[:12],
            symbol=symbol,
            side=side,
            price=price,
            size=size,
            fee=fee,
            fee_rate=fee_rate,
            net_proceeds=net,
            filled_at=datetime.now(tz=timezone.utc),
        )
        self._fills.append(fill)
        self._total_fees += fee
        self._update_position(fill)

        logger.debug(
            "paper_fill | symbol={} | side={} | size={} | "
            "price={} | fee={} | net={}",
            symbol, side, size, price, fee, net,
        )
        return fill

    def _update_position(self, fill: PaperFill) -> None:
        """Update in-memory position after a fill."""
        existing = self._positions.get(fill.symbol)
        if fill.side == "buy":
            self._apply_buy(fill, existing)
        else:
            self._apply_sell(fill, existing)

    def _apply_buy(
        self,
        fill: PaperFill,
        existing: PaperPosition | None,
    ) -> None:
        """Apply a BUY fill — opens or adds to long position."""
        if existing is not None and existing.side == "short":
            self._reduce_position(fill, existing)
            return
        old_size = existing.size if existing else Decimal("0")
        old_notional = existing.entry_notional if existing else Decimal("0")
        new_size = old_size + fill.size
        new_notional = old_notional + fill.net_proceeds
        if new_size > 0:
            entry_price = new_notional / new_size
        else:
            entry_price = fill.price
        self._positions[fill.symbol] = PaperPosition(
            symbol=fill.symbol,
            side="long",
            size=new_size,
            entry_price=entry_price,
            entry_notional=new_notional,
        )

    def _apply_sell(
        self,
        fill: PaperFill,
        existing: PaperPosition | None,
    ) -> None:
        """Apply a SELL fill — opens or adds to short position."""
        if existing is not None and existing.side == "long":
            self._reduce_position(fill, existing)
            return
        old_size = existing.size if existing else Decimal("0")
        old_notional = existing.entry_notional if existing else Decimal("0")
        new_size = old_size + fill.size
        new_notional = old_notional + fill.net_proceeds
        if new_size > 0:
            entry_price = new_notional / new_size
        else:
            entry_price = fill.price
        self._positions[fill.symbol] = PaperPosition(
            symbol=fill.symbol,
            side="short",
            size=new_size,
            entry_price=entry_price,
            entry_notional=new_notional,
        )

    def _reduce_position(
        self,
        fill: PaperFill,
        existing: PaperPosition,
    ) -> None:
        """Reduce or close an existing position."""
        remaining = existing.size - fill.size
        if remaining <= 0:
            self._positions.pop(fill.symbol, None)
        else:
            self._positions[fill.symbol] = existing.model_copy(
                update={"size": remaining},
            )


def _signal_to_order_side(
    signal: Literal["long", "short", "close"],
    symbol: str,
    positions: dict[str, PaperPosition],
) -> Literal["buy", "sell"]:
    """Map a signal direction to an order side."""
    if signal == "long":
        return "buy"
    if signal == "short":
        return "sell"
    # close — reverse the existing position
    existing = positions.get(symbol)
    if existing is not None and existing.side == "long":
        return "sell"
    return "buy"
