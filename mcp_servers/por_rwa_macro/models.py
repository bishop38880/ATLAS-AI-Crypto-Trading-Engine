"""
Pydantic v2 schemas for Proof of Reserve and RWA flow reports.

Section 5 Architecture: Macro Context — Institutional Rotation.
All financial values use ``decimal.Decimal`` to prevent precision loss
on uint256 token amounts. Models are frozen (immutable) per ATLAS convention.
These schemas are the canonical agentic contract consumed by
``MarketRegimeAgent`` and ``MacroCrossMarketAgent``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class RegimeSignal(str, Enum):
    """Deterministic macro regime classification."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    DEGRADED = "DEGRADED"


class PoRHealthReport(BaseModel, frozen=True):
    """
    Chainlink Proof of Reserve health for a single RWA token.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Consumed by ``MarketRegimeAgent`` as a deterministic de-peg / solvency signal.
    """

    symbol: str = Field(description="Canonical RWA symbol (e.g. BUIDL, USDY)")
    name: str = Field(description="Human-readable token name")
    on_chain_supply: Decimal = Field(
        description="Current totalSupply normalised to human-readable units"
    )
    off_chain_reserve: Decimal = Field(
        description="Latest Chainlink PoR oracle answer normalised to human-readable units"
    )
    collateralization_ratio: Decimal = Field(
        description="off_chain_reserve / on_chain_supply — below 1.0 is undercollateralised"
    )
    is_fully_backed: bool = Field(
        description="True if collateralization_ratio >= 1.0"
    )
    oracle_updated_at: datetime = Field(
        description="Timestamp of latest Chainlink round update (UTC)"
    )
    oracle_stale: bool = Field(
        description="True if oracle update is older than 24 hours"
    )
    status: str = Field(
        default="OK",
        description="OK | DEGRADED | NO_ORACLE — operational health flag",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC)",
    )


class RWAFlows(BaseModel, frozen=True):
    """
    Aggregated 24h and 7d mint/burn flow for a single RWA token.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Positive net_flow indicates minting dominance (capital inflow to RWA).
    Consumed by ``MacroCrossMarketAgent`` for velocity-based rotation detection.
    """

    symbol: str = Field(description="Canonical RWA symbol")
    mint_volume_24h: Decimal = Field(
        description="Total tokens minted in the last 24 hours (normalised)"
    )
    burn_volume_24h: Decimal = Field(
        description="Total tokens burned in the last 24 hours (normalised)"
    )
    net_flow_24h: Decimal = Field(
        description="net = mints - burns over 24h (positive = inflow)"
    )
    mint_volume_7d: Decimal = Field(
        description="Total tokens minted in the last 7 days (normalised)"
    )
    burn_volume_7d: Decimal = Field(
        description="Total tokens burned in the last 7 days (normalised)"
    )
    net_flow_7d: Decimal = Field(
        description="net = mints - burns over 7d (positive = inflow)"
    )
    avg_daily_net_flow_7d: Decimal = Field(
        description="7d net flow / 7 — baseline for velocity comparison"
    )
    status: str = Field(
        default="OK",
        description="OK | DEGRADED — operational health flag",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC)",
    )


class RWATokenSummary(BaseModel, frozen=True):
    """Per-token summary within the macro rotation report."""

    symbol: str = Field(description="Canonical RWA symbol")
    net_flow_24h: Decimal = Field(description="24h net flow (normalised)")
    net_flow_7d: Decimal = Field(description="7d net flow (normalised)")
    velocity_ratio: Decimal = Field(
        description="24h_flow / avg_daily_7d — measures acceleration"
    )
    is_fully_backed: bool = Field(description="PoR collateralisation check")


class MacroRotationReport(BaseModel, frozen=True):
    """
    Master aggregation: synthesises all tracked RWA flows into a regime signal.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    This is the top-level output consumed by ``MarketRegimeAgent``.
    A ``RISK_OFF`` signal indicates institutional capital is rotating
    from volatile crypto into tokenised Treasuries at an anomalous velocity.
    """

    regime_signal: RegimeSignal = Field(
        description="Deterministic signal: RISK_ON | RISK_OFF | NEUTRAL | DEGRADED"
    )
    total_net_inflow_24h: Decimal = Field(
        description="Sum of 24h net flows across all tracked RWA tokens"
    )
    total_net_inflow_7d: Decimal = Field(
        description="Sum of 7d net flows across all tracked RWA tokens"
    )
    velocity_ratio_aggregate: Decimal = Field(
        description="Aggregate 24h / daily_avg_7d velocity ratio"
    )
    token_summaries: list[RWATokenSummary] = Field(
        description="Per-token breakdown"
    )
    reasoning: str = Field(
        description="Human-readable explanation of the regime determination"
    )
    status: str = Field(
        default="OK",
        description="OK | DEGRADED — overall system health",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC)",
    )
