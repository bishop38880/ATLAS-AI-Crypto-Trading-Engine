"""Pydantic v2 frozen models for the Derivatives Context MCP Server.

These models structure the Perpetual Futures intelligence retrieved from Redis
for agentic consumption.
"""

from __future__ import annotations

from decimal import Decimal
from pydantic import BaseModel, Field


class UnifiedPerpState(BaseModel, frozen=True):
    """Aggregated perpetual futures state for a symbol."""

    symbol: str = Field(description="The trading symbol (e.g., BTC-PERP)")
    mark_price: Decimal = Field(description="The current mark price in USD")
    aggregated_oi_usd: Decimal = Field(description="Aggregated Open Interest in USD")
    annualized_funding_apy: Decimal = Field(
        description="Current annualized funding rate (as APY)"
    )


class LiquidationCluster(BaseModel, frozen=True):
    """A price band with concentrated liquidation potential."""

    price_band: Decimal = Field(description="The center of the price band")
    density_score: Decimal = Field(
        description="A score representing the concentration of liquidations (0-100)"
    )
    side: str = Field(description="The side of the liquidations: 'long' or 'short'")


class SqueezeRiskReport(BaseModel, frozen=True):
    """Detailed evaluation of squeeze risk and momentum veto status."""

    hazard_level: str = Field(
        description="Hazard level: 'LOW', 'MEDIUM', 'HIGH', or 'CRITICAL'"
    )
    momentum_veto: bool = Field(
        description="True if an active cascade is detected against the intended direction"
    )
    hydra_alert_active: bool = Field(
        description="True if HYDRA has a live cascade alert for this symbol"
    )
    rationale: str = Field(description="Detailed explanation for the hazard level and veto status")
