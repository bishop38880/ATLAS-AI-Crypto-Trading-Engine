"""Execution risk guard models — frozen Pydantic v2.

Pre-execution check inputs, decision outcomes, and result payloads.
All financial fields use ``Decimal``.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class RiskDecision(str, Enum):
    """Outcome of a pre-execution risk check."""

    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DOWNGRADED = "DOWNGRADED"


class RiskCheckInput(BaseModel, frozen=True):
    """Input payload for all pre-execution risk checks."""

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    side: Literal["long", "short"] = Field(description="Position direction")
    position_notional_usd: Decimal = Field(
        description="Proposed position size in USD",
    )
    portfolio_capital_usd: Decimal = Field(
        description="Total portfolio equity in USD",
    )
    leverage: Decimal = Field(description="Requested leverage multiplier")
    margin_mode: Literal["isolated", "crossed"] = Field(
        default="isolated",
        description="Margin mode for the order",
    )
    funding_rate_8h: Decimal = Field(
        default=Decimal("0"),
        description="Current 8-hour funding rate as decimal",
    )


class RiskCheckResult(BaseModel, frozen=True):
    """Outcome of a pre-execution risk check."""

    decision: RiskDecision = Field(description="Guard verdict")
    rule_name: str = Field(description="Which rule produced this decision")
    reason: str = Field(description="Human-readable explanation")
    original_leverage: Decimal = Field(
        description="Leverage before any downgrade",
    )
    adjusted_leverage: Decimal = Field(
        description="Leverage after downgrade (same if no change)",
    )
