"""Backtesting data models — frozen Pydantic v2.

Order book snapshots, simulated orders, fill results, and simulation
summaries.  All financial fields use ``Decimal``.  No ``float``
contamination in model definitions.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


# ── Order Book Models ────────────────────────────────────────────────


class PriceLevel(BaseModel, frozen=True):
    """Single price level in the order book."""

    price: Decimal = Field(description="Price at this level")
    size: Decimal = Field(description="Aggregate size at this level")


class OrderBookSnapshot(BaseModel, frozen=True):
    """Point-in-time snapshot of the order book."""

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    timestamp: datetime = Field(description="Snapshot timestamp")
    bids: list[PriceLevel] = Field(
        description="Bid levels, sorted price descending",
    )
    asks: list[PriceLevel] = Field(
        description="Ask levels, sorted price ascending",
    )
    regime: str = Field(default="trending", description="Market regime label")

    @property
    def best_bid(self) -> Decimal:
        """Highest bid price, or zero if no bids."""
        return self.bids[0].price if self.bids else Decimal("0")

    @property
    def best_ask(self) -> Decimal:
        """Lowest ask price, or zero if no asks."""
        return self.asks[0].price if self.asks else Decimal("0")

    @property
    def mid_price(self) -> Decimal:
        """Mid-point between best bid and best ask."""
        if not self.bids or not self.asks:
            return Decimal("0")
        return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self) -> Decimal:
        """Spread in basis points relative to mid price."""
        mid = self.mid_price
        if mid == 0:
            return Decimal("0")
        spread = self.best_ask - self.best_bid
        return (spread / mid * 10000).quantize(Decimal("0.01"))


# ── Simulated Order Models ──────────────────────────────────────────


class SimulatedOrder(BaseModel, frozen=True):
    """Order submitted by a simulated agent."""

    order_id: str = Field(description="Unique order identifier")
    order_type: Literal["MARKET", "LIMIT"] = Field(
        description="Order type",
    )
    side: Literal["buy", "sell"] = Field(description="Order side")
    size: Decimal = Field(description="Order size in base units")
    price: Decimal = Field(
        default=Decimal("0"),
        description="Limit price (ignored for MARKET orders)",
    )
    agent_id: str = Field(description="Originating agent identifier")
    timestamp: datetime = Field(description="Submission timestamp")


class FillResult(BaseModel, frozen=True):
    """Result of executing a simulated order against the book."""

    order_id: str = Field(description="Original order ID")
    filled_size: Decimal = Field(description="Total size filled")
    avg_fill_price: Decimal = Field(
        description="Volume-weighted average fill price",
    )
    slippage_bps: Decimal = Field(
        description="Slippage vs mid-price in basis points",
    )
    is_partial: bool = Field(description="True if order was partially filled")
    remaining_size: Decimal = Field(description="Unfilled remainder")
    levels_consumed: int = Field(
        description="Number of book levels consumed",
    )
    total_cost: Decimal = Field(
        description="Total execution cost (filled_size × avg_fill_price)",
    )


# ── Simulation Result Models ────────────────────────────────────────


class TradeSlippageRecord(BaseModel, frozen=True):
    """Single trade slippage observation for reporting."""

    order_id: str = Field(description="Order identifier")
    symbol: str = Field(description="Trading pair")
    side: Literal["buy", "sell"] = Field(description="Order side")
    requested_size: Decimal = Field(description="Requested order size")
    filled_size: Decimal = Field(description="Actual filled size")
    mid_price_at_entry: Decimal = Field(
        description="Mid price when order was submitted",
    )
    avg_fill_price: Decimal = Field(
        description="VWAP of fills",
    )
    slippage_bps: Decimal = Field(description="Realised slippage in bps")
    is_partial: bool = Field(description="Whether fill was partial")
    timestamp: datetime = Field(description="Execution timestamp")
    agent_id: str = Field(default="unknown", description="Agent that originated this trade")
    regime: str = Field(default="trending", description="Regime during execution")


class SimulationResult(BaseModel, frozen=True):
    """Aggregate result of an agent-based simulation run."""

    symbol: str = Field(description="Simulated symbol")
    seed: int = Field(description="RNG seed used for reproducibility")
    total_trades: int = Field(description="Total orders executed")
    total_fills: int = Field(description="Total fills (inc. partials)")
    avg_slippage_bps: Decimal = Field(
        description="Mean slippage across all fills",
    )
    max_slippage_bps: Decimal = Field(
        description="Maximum slippage observed",
    )
    partial_fill_rate: Decimal = Field(
        description="Fraction of orders partially filled",
    )
    trades: list[TradeSlippageRecord] = Field(
        default_factory=list,
        description="Per-trade slippage records",
    )
    wall_clock_seconds: Decimal = Field(
        description="Wall-clock time for the simulation",
    )
    simulated_duration_seconds: Decimal = Field(
        description="Simulated market time covered",
    )
    speedup_factor: Decimal = Field(
        description="simulated_duration / wall_clock (target >10x)",
    )
    performance: PerformanceAttribution | None = Field(
        default=None,
        description="Post-simulation performance analysis",
    )


class SlippageComparison(BaseModel, frozen=True):
    """Comparison of simulated vs actual slippage for a trade."""

    order_id: str = Field(description="Trade identifier")
    simulated_slippage_bps: Decimal = Field(
        description="Slippage from simulation",
    )
    actual_slippage_bps: Decimal = Field(
        description="Slippage from exchange fill logs",
    )
    delta_bps: Decimal = Field(
        description="Difference (simulated - actual)",
    )
    within_tolerance: bool = Field(
        description="True if |delta| <= 10% of actual",
    )


class PerformanceAttribution(BaseModel, frozen=True):
    """Financial performance metrics for the simulation."""

    initial_equity: Decimal = Field(description="Starting capital")
    final_equity: Decimal = Field(description="Ending capital")
    total_pnl: Decimal = Field(description="Absolute P&L")
    pnl_pct: Decimal = Field(description="Return percentage")
    max_drawdown_pct: Decimal = Field(description="Maximum observed drawdown %")
    sharpe_ratio: Decimal = Field(
        description="Annualised Sharpe ratio (estimated)",
    )
    total_fees: Decimal = Field(description="Total trading fees paid")
    win_rate: Decimal = Field(description="Fraction of profitable trades")
    profit_factor: Decimal = Field(description="Gross profit / Gross loss")
    total_volume: Decimal = Field(description="Total traded volume")
    agent_stats: dict[str, AgentPerformance] = Field(
        default_factory=dict,
        description="Per-agent performance breakdown",
    )
    regime_stats: dict[str, RegimePerformance] = Field(
        default_factory=dict,
        description="Per-regime performance breakdown",
    )


class AgentPerformance(BaseModel, frozen=True):
    """Performance metrics attributed to a specific agent."""

    agent_id: str = Field(description="Agent identifier")
    total_pnl: Decimal = Field(description="P&L attributed to this agent")
    total_volume: Decimal = Field(description="Total volume traded by this agent")
    trade_count: int = Field(description="Number of trades executed by this agent")
    avg_slippage_bps: Decimal = Field(description="Average slippage for this agent")


class RegimePerformance(BaseModel, frozen=True):
    """Performance metrics attributed to a specific market regime."""

    regime: str = Field(description="Regime identifier")
    total_pnl: Decimal = Field(description="P&L attributed to this regime")
    trade_count: int = Field(description="Number of trades in this regime")
    avg_slippage_bps: Decimal = Field(description="Average slippage in this regime")
