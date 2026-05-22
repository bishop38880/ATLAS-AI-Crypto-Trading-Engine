"""Paper trading data models — frozen Pydantic v2.

Simulated fills, positions, and portfolio state.
All financial fields use ``Decimal``.  No ``float`` contamination.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PaperFill(BaseModel, frozen=True):
    """A single simulated fill event."""

    fill_id: str = Field(description="Unique fill identifier")
    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    side: Literal["buy", "sell"] = Field(description="Fill side")
    price: Decimal = Field(description="Execution price")
    size: Decimal = Field(description="Fill size in base coin")
    fee: Decimal = Field(description="Fee deducted (in quote coin)")
    fee_rate: Decimal = Field(description="Applied fee rate")
    net_proceeds: Decimal = Field(
        description="Size * price - fee (buy) or size * price + fee (sell)",
    )
    filled_at: datetime = Field(description="Simulation timestamp")


class PaperPosition(BaseModel, frozen=True):
    """A simulated open position."""

    symbol: str = Field(description="Trading pair")
    side: Literal["long", "short"] = Field(description="Position direction")
    size: Decimal = Field(description="Position size in base coin")
    entry_price: Decimal = Field(description="Weighted average entry price")
    entry_notional: Decimal = Field(description="Total entry cost in quote")
    unrealized_pnl: Decimal = Field(
        default=Decimal("0"),
        description="Current unrealized P&L in quote",
    )


class PaperPortfolio(BaseModel, frozen=True):
    """Snapshot of the simulated portfolio state."""

    capital_usd: Decimal = Field(description="Available capital in USDT")
    positions: list[PaperPosition] = Field(
        default_factory=list,
        description="All open simulated positions",
    )
    total_fees_paid: Decimal = Field(
        default=Decimal("0"),
        description="Cumulative fees paid across all fills",
    )
    fill_count: int = Field(
        default=0,
        description="Total number of fills executed",
    )
