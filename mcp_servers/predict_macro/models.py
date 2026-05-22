"""
Pydantic DTOs for the Fiat Gravity Engine MCP.

MacroCrossMarketAgent - Fiat Gravity Engine: a ``fiat_gravity_score`` near
``100.0`` indicates aggressive global liquidity expansion (money-printing
impulse aligned with stablecoin minting), mandating maximum risk-on posture
for downstream regime agents.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class TradFiState(BaseModel, frozen=True):
    """Latest TradFi baseline plus trailing 30-day percentage changes."""

    dxy_latest: float = Field(description="Broad dollar index (latest observation).")
    dxy_pct_change_30d: float = Field(
        description="30-trading-day approximate percentage change for dollar index."
    )
    us10y_latest: float = Field(description="10-year Treasury yield, percent.")
    us10y_pct_change_30d: float = Field(
        description="30-day percentage change in 10-year yield."
    )
    sofr_latest: float = Field(description="SOFR, percent.")
    sofr_pct_change_30d: float = Field(description="30-day percentage change in SOFR.")
    m2_latest: float = Field(description="M2 stock level (FRED units).")
    m2_pct_change_30d: float = Field(description="30-day percentage change in M2.")
    observation_timestamp_utc: datetime = Field(
        description="UTC time when this snapshot was assembled."
    )
    status: Literal["OK", "DEGRADED"] = Field(description="Data health flag.")


class StablecoinFlows(BaseModel, frozen=True):
    """Net mint-minus-burn USD flows for USDT and USDC."""

    usdt_net_usd_24h: Decimal = Field(description="USDT net minted minus burned, 24h USD.")
    usdt_net_usd_7d: Decimal = Field(description="USDT net USD over 7 days.")
    usdt_net_usd_30d: Decimal = Field(description="USDT net USD over 30 days.")
    usdc_net_usd_24h: Decimal = Field(description="USDC net USD over 24 hours.")
    usdc_net_usd_7d: Decimal = Field(description="USDC net USD over 7 days.")
    usdc_net_usd_30d: Decimal = Field(description="USDC net USD over 30 days.")
    combined_net_usd_24h: Decimal = Field(description="USDT+USDC net USD, 24h.")
    combined_net_usd_7d: Decimal = Field(description="USDT+USDC net USD, 7d.")
    combined_net_usd_30d: Decimal = Field(description="USDT+USDC net USD, 30d.")
    status: Literal["OK", "DEGRADED"] = Field(description="Cache health flag.")


class LiquidityRegimeReport(BaseModel, frozen=True):
    """Master liquidity synthesis for MacroCrossMarketAgent consumption."""

    fiat_gravity_score: float = Field(
        ge=0.0,
        le=100.0,
        description=(
            "0 = severe liquidity contraction / risk-off; 100 = aggressive expansion / "
            "risk-on (central-bank impulse plus stablecoin injection)."
        ),
    )
    regime_signal: Literal["EXPANSION", "CONTRACTION", "NEUTRAL"] = Field(
        description="Deterministic discrete regime classification."
    )
    correlation_tradfi_yield_vs_stablecoin_mints_30d: float = Field(
        description=(
            "Pearson correlation over ~30 aligned calendar days between a TradFi "
            "yield-compression factor and daily stablecoin net minting."
        )
    )
    reasoning: str = Field(description="Human-readable synthesis for agents.")
    status: Literal["OK", "DEGRADED"] = Field(description="Report health flag.")
