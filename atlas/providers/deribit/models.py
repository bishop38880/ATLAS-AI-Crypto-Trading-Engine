"""Pydantic models for Deribit options chain and intelligence snapshots."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class OptionsChainSnapshot(BaseModel, frozen=True):
    """Aggregated options chain for one asset across active expiries."""

    asset: str = Field(description="BTC or ETH")
    expiry: str = Field(description="ISO date or ALL when merged across expiries")
    strikes: list[Decimal] = Field(description="Strike prices sorted ascending")
    call_oi: list[Decimal] = Field(description="Call open interest per strike")
    put_oi: list[Decimal] = Field(description="Put open interest per strike")
    call_volume_24h: list[Decimal] = Field(description="24h call volume per strike")
    put_volume_24h: list[Decimal] = Field(description="24h put volume per strike")
    iv_call: list[Decimal] = Field(description="Call mark IV per strike")
    iv_put: list[Decimal] = Field(description="Put mark IV per strike")
    spot_price: Decimal = Field(description="Underlying spot at snapshot time")
    timestamp_utc: str = Field(description="ISO-8601 UTC snapshot timestamp")


class TermStructurePoint(BaseModel, frozen=True):
    """At-the-money implied vol for one expiry bucket."""

    expiry_days: int = Field(description="Days to expiry")
    atm_iv: Decimal = Field(description="At-the-money implied volatility")
    expiry_label: str = Field(description="Human label e.g. 1W, 2W, 1M, 3M")


class OptionsIntelligence(BaseModel, frozen=True):
    """Derived options metrics for agent scoring."""

    asset: str = Field(description="BTC or ETH")
    max_pain_price: Decimal = Field(
        description="Strike minimising total options holder loss at expiry",
    )
    put_call_ratio_volume: Decimal = Field(
        description="24h put volume divided by 24h call volume",
    )
    put_call_ratio_oi: Decimal = Field(
        description="Put OI divided by call OI across all strikes",
    )
    term_structure: list[TermStructurePoint] = Field(
        description="ATM IV by expiry bucket",
    )
    iv_skew: Decimal = Field(
        description="25-delta put IV minus 25-delta call IV (vol points)",
    )
    contango: bool = Field(
        description="True when longer-dated ATM IV exceeds near-term ATM IV",
    )
    spot_to_max_pain_pct: Decimal = Field(
        description="(spot - max_pain) / spot * 100",
    )
