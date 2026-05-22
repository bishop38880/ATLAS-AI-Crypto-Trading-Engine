"""Dune Analytics data models — frozen Pydantic v2 schemas.

All financial fields are Decimal. No float for USD, percentages, or rates.

Architecture note:
    TokenMetricsSnapshot is the canonical return type for DuneMCPProvider.fetch_data().
    DuneSnapshot is retained as an alias for backward compatibility.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DEXVolumeData(BaseModel):
    """DEX volume data for an asset."""

    model_config = ConfigDict(frozen=True)

    volume_24h_usd: Decimal = Field(default=Decimal("0"))
    volume_7d_usd: Decimal = Field(default=Decimal("0"))
    market_share_pct: Decimal = Field(default=Decimal("0"))


class StablecoinFlowData(BaseModel):
    """Stablecoin net flow data."""

    model_config = ConfigDict(frozen=True)

    net_flow_24h_usd: Decimal = Field(default=Decimal("0"))
    net_flow_7d_usd: Decimal = Field(default=Decimal("0"))
    total_supply_usd: Decimal = Field(default=Decimal("0"))


class PerpOIData(BaseModel):
    """Perpetual futures Open Interest data."""

    model_config = ConfigDict(frozen=True)

    total_oi_usd: Decimal = Field(default=Decimal("0"))
    oi_change_24h_pct: Decimal = Field(default=Decimal("0"))


class TokenUnlockEvent(BaseModel):
    """Token unlock schedule event."""

    model_config = ConfigDict(frozen=True)

    asset: str
    unlock_pct_of_supply: Decimal = Field(default=Decimal("0"))
    unlock_usd_value: Decimal = Field(default=Decimal("0"))
    days_until_unlock: int = 0
    unlock_type: Literal["CLIFF", "LINEAR", "UNKNOWN"] = "UNKNOWN"


class WhaleFlowData(BaseModel):
    """Whale accumulation/distribution data."""

    model_config = ConfigDict(frozen=True)

    net_flow_24h_usd: Decimal = Field(default=Decimal("0"))
    direction: Literal["ACCUMULATING", "DISTRIBUTING", "NEUTRAL"] = "NEUTRAL"
    whale_dominance_pct: Decimal = Field(default=Decimal("0"))


class DuneSnapshot(BaseModel):
    """Aggregated Dune Analytics snapshot for an asset.

    Also known as TokenMetricsSnapshot. Contains protocol TVL proxies,
    active user signals, and token velocity metrics sourced from Dune MCP.
    """

    model_config = ConfigDict(frozen=True)

    asset: str
    dex_volume: DEXVolumeData = Field(default_factory=DEXVolumeData)
    stablecoin_flow: StablecoinFlowData = Field(default_factory=StablecoinFlowData)
    perp_oi: PerpOIData = Field(default_factory=PerpOIData)
    whale_flow: WhaleFlowData = Field(default_factory=WhaleFlowData)
    token_unlocks: list[TokenUnlockEvent] = Field(default_factory=list)
    stale: bool = False
    status: Literal["healthy", "degraded"] = "healthy"


# Canonical alias referenced in the prompt specification.
TokenMetricsSnapshot = DuneSnapshot
