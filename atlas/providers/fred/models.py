"""FRED macroeconomic data models — frozen Pydantic v2 schemas.

All financial fields are Decimal. No float for yields, rates, or supply metrics.

Architecture note:
    MacroSnapshot is the canonical return type for FREDProvider.fetch_data().
    The model_validator classifies macro regime before construction so that
    the frozen model is fully determined at instantiation time.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FREDObservation(BaseModel):
    """A single observation from FRED."""

    model_config = ConfigDict(frozen=True)

    date: str
    value: Decimal = Field(default=Decimal("0"))


class YieldCurveData(BaseModel):
    """Yield curve data (10Y - 2Y treasury spread)."""

    model_config = ConfigDict(frozen=True)

    ten_year: Decimal = Field(default=Decimal("0"))
    two_year: Decimal = Field(default=Decimal("0"))
    spread: Decimal = Field(default=Decimal("0"))


class MacroSnapshot(BaseModel):
    """Aggregated Macro snapshot from FRED.

    Contains US Treasury yields, stablecoin supply metrics, CPI,
    and an auto-classified macro regime for the NewsMacroAgent.
    """

    model_config = ConfigDict(frozen=True)

    fed_funds_rate: Decimal = Field(default=Decimal("0"))
    yield_curve: YieldCurveData = Field(default_factory=YieldCurveData)
    cpi_yoy: Decimal = Field(default=Decimal("0"))
    stablecoin_total_supply_usd: Decimal = Field(default=Decimal("0"))
    macro_regime: Literal["RISK_ON", "RISK_OFF", "NEUTRAL"] = "NEUTRAL"
    stale: bool = False
    status: Literal["healthy", "degraded"] = "healthy"

    @model_validator(mode="before")
    @classmethod
    def classify_regime(cls, values: dict) -> dict:  # type: ignore[override]
        """Classify macro regime before construction (frozen-compatible)."""
        if not isinstance(values, dict):
            return values
        yc = values.get("yield_curve")
        if isinstance(yc, dict):
            spread = Decimal(str(yc.get("spread", 0)))
            ffr = Decimal(str(values.get("fed_funds_rate", 0)))
        elif isinstance(yc, YieldCurveData):
            spread = yc.spread
            ffr = Decimal(str(values.get("fed_funds_rate", 0)))
        else:
            return values
        if spread < Decimal("0") and ffr > Decimal("4.0"):
            values["macro_regime"] = "RISK_OFF"
        elif spread > Decimal("0.5") and ffr < Decimal("3.0"):
            values["macro_regime"] = "RISK_ON"
        else:
            values["macro_regime"] = "NEUTRAL"
        return values
