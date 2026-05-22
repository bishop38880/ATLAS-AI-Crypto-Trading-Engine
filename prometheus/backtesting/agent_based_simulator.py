"""Agent-based order book simulator — Phase 9 core.

Simulated agents submit orders against a mutable order book that
is re-seeded from the replay engine at each tick.  MARKET orders
consume the book (walk levels); LIMIT orders rest on the book.

Deterministic via seeded ``numpy.random.Generator``.
All financial results in ``Decimal``.
"""

from __future__ import annotations

import time

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import numpy as np
from loguru import logger

from prometheus.backtesting.models import (
    FillResult,
    OrderBookSnapshot,
    SimulatedOrder,
    SimulationResult,
    TradeSlippageRecord,
    PerformanceAttribution,
)
from prometheus.backtesting.order_book_replay import OrderBookReplayEngine
from prometheus.backtesting.performance import PerformanceCalculator


# ── Mutable Order Book ───────────────────────────────────────────────


class MutableOrderBook:
    """In-memory order book that agents modify during simulation.

    Bid/ask levels are stored as lists of ``[price, size]`` pairs.
    Bids sorted descending by price; asks sorted ascending.
    """

    def __init__(self, snapshot: OrderBookSnapshot) -> None:
        self._symbol = snapshot.symbol
        self._bids: list[list[Decimal]] = [
            [lv.price, lv.size] for lv in snapshot.bids
        ]
        self._asks: list[list[Decimal]] = [
            [lv.price, lv.size] for lv in snapshot.asks
        ]

    @property
    def mid_price(self) -> Decimal:
        """Current mid-price, or zero if book is empty."""
        if not self._bids or not self._asks:
            return Decimal("0")
        return (self._bids[0][0] + self._asks[0][0]) / Decimal("2")

    # ── Market Order Execution ───────────────────────────────────────

    def consume_market_buy(
        self,
        size: Decimal,
    ) -> FillResult:
        """Walk asks to fill a BUY market order."""
        return self._walk_book(
            self._asks, size, order_side="buy",
        )

    def consume_market_sell(
        self,
        size: Decimal,
    ) -> FillResult:
        """Walk bids to fill a SELL market order."""
        return self._walk_book(
            self._bids, size, order_side="sell",
        )

    def _walk_book(
        self,
        levels: list[list[Decimal]],
        remaining: Decimal,
        order_side: str,
    ) -> FillResult:
        """Consume levels until ``remaining`` is filled or exhausted."""
        mid_before = self.mid_price
        filled = Decimal("0")
        cost = Decimal("0")
        consumed = 0

        while remaining > 0 and levels:
            level = levels[0]
            take = min(remaining, level[1])
            cost += take * level[0]
            filled += take
            level[1] -= take
            remaining -= take
            if level[1] <= 0:
                levels.pop(0)
                consumed += 1

        return _build_fill_result(
            filled, cost, remaining, consumed, mid_before, order_side,
        )

    # ── Limit Order Placement ────────────────────────────────────────

    def add_limit_buy(self, price: Decimal, size: Decimal) -> None:
        """Insert a resting BUY limit order into the bid side."""
        _insert_level_desc(self._bids, price, size)

    def add_limit_sell(self, price: Decimal, size: Decimal) -> None:
        """Insert a resting SELL limit order into the ask side."""
        _insert_level_asc(self._asks, price, size)

    def match_crossing_limits(self) -> list[FillResult]:
        """Cross any bids >= best ask (aggressive limits)."""
        fills: list[FillResult] = []
        while self._bids and self._asks:
            if self._bids[0][0] < self._asks[0][0]:
                break
            size = min(self._bids[0][1], self._asks[0][1])
            mid = self.mid_price
            cost = size * self._asks[0][0]
            self._bids[0][1] -= size
            self._asks[0][1] -= size
            if self._bids[0][1] <= 0:
                self._bids.pop(0)
            if self._asks[0][1] <= 0:
                self._asks.pop(0)
            fills.append(_build_fill_result(
                size, cost, Decimal("0"), 1, mid, "cross",
            ))
        return fills


# ── Fill Result Builder ──────────────────────────────────────────────


def _build_fill_result(
    filled: Decimal,
    cost: Decimal,
    remaining: Decimal,
    levels_consumed: int,
    mid_before: Decimal,
    order_side: str,
) -> FillResult:
    """Construct a FillResult with slippage calculation."""
    if filled > 0:
        avg_price = (cost / filled).quantize(Decimal("0.00000001"))
    else:
        avg_price = Decimal("0")
    slippage = _calculate_slippage_bps(avg_price, mid_before, order_side)
    return FillResult(
        order_id="",
        filled_size=filled,
        avg_fill_price=avg_price,
        slippage_bps=slippage,
        is_partial=remaining > 0,
        remaining_size=remaining,
        levels_consumed=levels_consumed,
        total_cost=cost,
    )


def _calculate_slippage_bps(
    avg_price: Decimal,
    mid_price: Decimal,
    side: str,
) -> Decimal:
    """Slippage in basis points relative to mid-price."""
    if mid_price == 0:
        return Decimal("0")
    if side == "buy":
        raw = (avg_price - mid_price) / mid_price * Decimal("10000")
    elif side == "sell":
        raw = (mid_price - avg_price) / mid_price * Decimal("10000")
    else:
        raw = abs(avg_price - mid_price) / mid_price * Decimal("10000")
    return raw.quantize(Decimal("0.01"))


# ── Level Insertion Helpers ──────────────────────────────────────────


def _insert_level_desc(
    levels: list[list[Decimal]],
    price: Decimal,
    size: Decimal,
) -> None:
    """Insert into descending-sorted bid levels."""
    for i, lv in enumerate(levels):
        if price == lv[0]:
            lv[1] += size
            return
        if price > lv[0]:
            levels.insert(i, [price, size])
            return
    levels.append([price, size])


def _insert_level_asc(
    levels: list[list[Decimal]],
    price: Decimal,
    size: Decimal,
) -> None:
    """Insert into ascending-sorted ask levels."""
    for i, lv in enumerate(levels):
        if price == lv[0]:
            lv[1] += size
            return
        if price < lv[0]:
            levels.insert(i, [price, size])
            return
    levels.append([price, size])


# ── Simulated Agents ─────────────────────────────────────────────────


class SimulatedAgent(ABC):
    """Protocol for agents that submit orders during simulation."""

    @abstractmethod
    def generate_orders(
        self,
        timestamp: datetime,
        snapshot: OrderBookSnapshot,
        rng: np.random.Generator,
    ) -> list[SimulatedOrder]:
        """Return orders for this tick. May return empty list."""


class PolarisSignalAgent(SimulatedAgent):
    """Replays pre-defined POLARIS signals as market orders."""

    def __init__(
        self,
        signals: list[tuple[datetime, Literal["buy", "sell"], Decimal]],
    ) -> None:
        self._signals: dict[datetime, tuple[Literal["buy", "sell"], Decimal]] = {
            ts: (side, size) for ts, side, size in signals
        }
        self._agent_id = "polaris"

    def generate_orders(
        self,
        timestamp: datetime,
        snapshot: OrderBookSnapshot,
        rng: np.random.Generator,
    ) -> list[SimulatedOrder]:
        """Emit a MARKET order if a signal fires at this timestamp."""
        entry = self._signals.get(timestamp)
        if entry is None:
            return []
        side, size = entry
        return [SimulatedOrder(
            order_id=format(rng.integers(0, 2**48), '012x'),
            order_type="MARKET",
            side=side,
            size=size,
            agent_id=self._agent_id,
            timestamp=timestamp,
        )]


class NoiseTrader(SimulatedAgent):
    """Provides background liquidity via random limit orders."""

    def __init__(
        self,
        order_rate: float = 0.1,
        max_size: float = 1.0,
    ) -> None:
        self._order_rate = order_rate
        self._max_size = max_size
        self._agent_id = "noise"

    def generate_orders(
        self,
        timestamp: datetime,
        snapshot: OrderBookSnapshot,
        rng: np.random.Generator,
    ) -> list[SimulatedOrder]:
        """Submit random limit orders at configured rate."""
        if rng.random() > self._order_rate:
            return []
        return [self._random_limit_order(timestamp, snapshot, rng)]

    def _random_limit_order(
        self,
        timestamp: datetime,
        snapshot: OrderBookSnapshot,
        rng: np.random.Generator,
    ) -> SimulatedOrder:
        """Generate a single random limit order near the spread."""
        mid = float(snapshot.mid_price)
        spread = float(snapshot.spread_bps)
        offset_bps = rng.uniform(-spread * 2, spread * 2)
        price = mid * (1 + offset_bps / 10000)
        size = rng.uniform(0.01, self._max_size)
        side: Literal["buy", "sell"] = "buy" if offset_bps < 0 else "sell"
        return SimulatedOrder(
            order_id=format(rng.integers(0, 2**48), '012x'),
            order_type="LIMIT",
            side=side,
            size=Decimal(str(round(size, 8))),
            price=Decimal(str(round(price, 8))),
            agent_id=self._agent_id,
            timestamp=timestamp,
        )


# ── Main Simulator ───────────────────────────────────────────────────


class AgentBasedSimulator:
    """Run agent-based simulation over replayed order book data.

    Deterministic: all randomness flows through a seeded
    ``numpy.random.Generator``.
    """

    def __init__(
        self,
        replay_engine: OrderBookReplayEngine,
        agents: list[SimulatedAgent],
        seed: int = 42,
    ) -> None:
        self._replay = replay_engine
        self._agents = agents
        self._seed = seed
        self._rng = np.random.default_rng(seed)

    def run(self) -> SimulationResult:
        """Execute the full simulation. Returns aggregate results."""
        wall_start = time.monotonic()
        records: list[TradeSlippageRecord] = []
        symbol = ""

        for timestamp, snapshot in self._replay:
            symbol = snapshot.symbol
            book = MutableOrderBook(snapshot)
            tick_records = self._process_tick(timestamp, snapshot, book)
            records.extend(tick_records)

        wall_seconds = time.monotonic() - wall_start
        
        # Calculate performance metrics (Phase 10)
        perf_calc = PerformanceCalculator()
        performance = perf_calc.calculate(records)
        
        return self._build_result(symbol, records, wall_seconds, performance)

    def _process_tick(
        self,
        timestamp: datetime,
        snapshot: OrderBookSnapshot,
        book: MutableOrderBook,
    ) -> list[TradeSlippageRecord]:
        """Collect and execute all agent orders for one tick."""
        records: list[TradeSlippageRecord] = []
        for agent in self._agents:
            orders = agent.generate_orders(timestamp, snapshot, self._rng)
            for order in orders:
                record = _execute_order(order, book, snapshot)
                if record is not None:
                    records.append(record)
        book.match_crossing_limits()
        return records

    def _build_result(
        self,
        symbol: str,
        records: list[TradeSlippageRecord],
        wall_seconds: float,
        performance: PerformanceAttribution,
    ) -> SimulationResult:
        """Aggregate per-trade records into a SimulationResult."""
        sim_duration = _compute_sim_duration(self._replay)
        return _assemble_simulation_result(
            symbol, self._seed, records, wall_seconds, sim_duration, performance
        )


# ── Order Execution ──────────────────────────────────────────────────


def _execute_order(
    order: SimulatedOrder,
    book: MutableOrderBook,
    snapshot: OrderBookSnapshot,
) -> TradeSlippageRecord | None:
    """Execute a single order against the mutable book."""
    if order.order_type == "LIMIT":
        _place_limit(order, book)
        return None
    return _execute_market(order, book, snapshot)


def _place_limit(order: SimulatedOrder, book: MutableOrderBook) -> None:
    """Place a limit order on the book."""
    if order.side == "buy":
        book.add_limit_buy(order.price, order.size)
    else:
        book.add_limit_sell(order.price, order.size)


def _execute_market(
    order: SimulatedOrder,
    book: MutableOrderBook,
    snapshot: OrderBookSnapshot,
) -> TradeSlippageRecord:
    """Execute a market order and return a slippage record."""
    mid = snapshot.mid_price
    if order.side == "buy":
        fill = book.consume_market_buy(order.size)
    else:
        fill = book.consume_market_sell(order.size)

    logger.debug(
        "sim_fill | order_id={} | side={} | filled={} | slip_bps={}",
        order.order_id, order.side, fill.filled_size, fill.slippage_bps,
    )

    return TradeSlippageRecord(
        order_id=order.order_id,
        symbol=snapshot.symbol,
        side=order.side,
        requested_size=order.size,
        filled_size=fill.filled_size,
        mid_price_at_entry=mid,
        avg_fill_price=fill.avg_fill_price,
        slippage_bps=fill.slippage_bps,
        is_partial=fill.is_partial,
        timestamp=order.timestamp,
        agent_id=order.agent_id,
        regime=snapshot.regime,
    )


# ── Result Assembly ──────────────────────────────────────────────────


def _compute_sim_duration(
    replay: OrderBookReplayEngine,
) -> float:
    """Simulated wall-clock duration in seconds."""
    if len(replay) < 2:
        return 0.0
    delta = replay.end_time - replay.start_time
    return delta.total_seconds()


def _assemble_simulation_result(
    symbol: str,
    seed: int,
    records: list[TradeSlippageRecord],
    wall_seconds: float,
    sim_duration: float,
    performance: PerformanceAttribution,
) -> SimulationResult:
    """Build the final SimulationResult from trade records."""
    total = len(records)
    if total == 0:
        return _empty_result(symbol, seed, wall_seconds, sim_duration, performance)

    slippages = [r.slippage_bps for r in records]
    partials = sum(1 for r in records if r.is_partial)
    speedup = sim_duration / wall_seconds if wall_seconds > 0 else 0
    
    avg_slippage = (sum(slippages) / Decimal(str(total))).quantize(Decimal("0.01"))
    max_slippage = max(slippages).quantize(Decimal("0.01"))

    return SimulationResult(
        symbol=symbol,
        seed=seed,
        total_trades=total,
        total_fills=total,
        avg_slippage_bps=avg_slippage,
        max_slippage_bps=max_slippage,
        partial_fill_rate=(Decimal(str(partials)) / Decimal(str(total))).quantize(Decimal("0.0001")),
        trades=records,
        wall_clock_seconds=Decimal(str(round(wall_seconds, 4))),
        simulated_duration_seconds=Decimal(str(round(sim_duration, 2))),
        speedup_factor=Decimal(str(round(speedup, 2))),
        performance=performance,
    )


def _empty_result(
    symbol: str,
    seed: int,
    wall_seconds: float,
    sim_duration: float,
    performance: PerformanceAttribution,
) -> SimulationResult:
    """Return a zero-valued SimulationResult when no trades occur."""
    speedup = sim_duration / wall_seconds if wall_seconds > 0 else 0
    return SimulationResult(
        symbol=symbol,
        seed=seed,
        total_trades=0,
        total_fills=0,
        avg_slippage_bps=Decimal("0"),
        max_slippage_bps=Decimal("0"),
        partial_fill_rate=Decimal("0"),
        trades=[],
        wall_clock_seconds=Decimal(str(round(wall_seconds, 4))),
        simulated_duration_seconds=Decimal(str(round(sim_duration, 2))),
        speedup_factor=Decimal(str(round(speedup, 2))),
        performance=performance,
    )
