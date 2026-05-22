"""Nansen data models — strict typing and Decimal precision enforced.

Adheres to Sentinel v3.0 architectural invariants.

Model hierarchy:
  - FlowEntity: Generic flow triple (net/inflow/outflow) used by WhaleAgent.
  - SmartMoneyNetflow: DEX-level smart money activity tracker.
  - RiskIndicators: Placeholder for future Nansen risk signals.
  - SmartMoneyFlow: Token-level smart money flow with wallet count.
  - ExchangeNetflow: CEX net token movement with signal derivation.
  - SmartMoneyHolder: Individual whale/VC wallet intelligence.
  - NansenSnapshot: Complete intelligence bundle consumed by agents.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Shared flow primitives (consumed by WhaleAgent)
# ---------------------------------------------------------------------------


class FlowEntity(BaseModel, frozen=True):
    """Generic USD flow triple — whale, exchange, or smart money segment."""

    net_flow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    inflow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    outflow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))


class SmartMoneyNetflow(BaseModel, frozen=True):
    """DEX-level smart money activity for retail divergence detection."""

    net_flow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    unique_smart_wallets: int = 0


class RiskIndicators(BaseModel, frozen=True):
    """Placeholder for future Nansen risk intelligence signals."""

    concentration_risk: float = 0.0
    whale_dominance_pct: float = 0.0


# ---------------------------------------------------------------------------
# Provider-level models (consumed by OnChainAgent / NansenProvider)
# ---------------------------------------------------------------------------


class SmartMoneyFlow(BaseModel, frozen=True):
    """Net USD flow from Nansen-labelled smart money wallets."""

    asset: str
    chain: str
    net_flow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    net_flow_usd_7d: Decimal = Field(default_factory=lambda: Decimal("0"))
    unique_smart_wallets: int = 0
    flow_direction: str = "NEUTRAL"  # "ACCUMULATING" | "DISTRIBUTING" | "NEUTRAL"
    time_range: str = "24h"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExchangeNetflow(BaseModel, frozen=True):
    """Net token movement to/from centralised exchanges."""

    asset: str
    netflow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))  # negative = outflow (bullish)
    inflow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    outflow_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    signal: str = "NEUTRAL"  # "BULLISH" (outflow) | "BEARISH" (inflow) | "NEUTRAL"
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode="after")
    def _derive_signal(self) -> "ExchangeNetflow":
        """Deterministic signal derivation based on netflow."""
        # Use object.__setattr__ because frozen=True
        if self.netflow_usd < Decimal("-1000000"):
            object.__setattr__(self, "signal", "BULLISH")
        elif self.netflow_usd > Decimal("1000000"):
            object.__setattr__(self, "signal", "BEARISH")
        else:
            object.__setattr__(self, "signal", "NEUTRAL")
        return self


class SmartMoneyHolder(BaseModel, frozen=True):
    """Intelligence for a single tracked whale/smart wallet."""

    wallet_address: str
    label: str = "Unknown"
    balance_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    balance_change_24h_usd: Decimal = Field(default_factory=lambda: Decimal("0"))
    is_accumulating: bool = False


# ---------------------------------------------------------------------------
# Aggregate snapshot
# ---------------------------------------------------------------------------


class NansenSnapshot(BaseModel, frozen=True):
    """Complete Nansen intelligence bundle for an asset.

    Consumed by:
      - OnChainAgent: smart_money_flow_24h, exchange_netflow, smart_money_signal
      - WhaleAgent: whale_flows, smart_money_dex_activity, risk_indicators
    """

    asset: str

    # OnChainAgent fields (provider always fills; defaults for WhaleAgent compat)
    smart_money_flow_24h: SmartMoneyFlow = Field(
        default_factory=lambda: SmartMoneyFlow(asset="", chain=""),
    )
    smart_money_flow_7d: SmartMoneyFlow = Field(
        default_factory=lambda: SmartMoneyFlow(asset="", chain=""),
    )
    exchange_netflow: ExchangeNetflow = Field(
        default_factory=lambda: ExchangeNetflow(asset=""),
    )
    top_holders: list[SmartMoneyHolder] = Field(default_factory=list)

    # WhaleAgent fields
    whale_flows: FlowEntity = Field(default_factory=FlowEntity)
    exchange_flows: FlowEntity = Field(default_factory=FlowEntity)
    smart_money_flows: FlowEntity = Field(default_factory=FlowEntity)
    smart_money_dex_activity: SmartMoneyNetflow = Field(default_factory=SmartMoneyNetflow)
    risk_indicators: RiskIndicators = Field(default_factory=RiskIndicators)

    # Derived signals
    smart_money_signal: Literal["ACCUMULATING", "DISTRIBUTING", "NEUTRAL"] = "NEUTRAL"
    confidence_modifier: float = 1.0
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stale: bool = False
    status: str = "OK"  # "OK" | "STALE" | "ERROR"

    @model_validator(mode="after")
    def _derive_signals(self) -> "NansenSnapshot":
        """Deterministic signal and confidence derivation."""
        # Signal derivation
        net_24h = self.smart_money_flow_24h.net_flow_usd
        ex_signal = self.exchange_netflow.signal

        signal: Literal["ACCUMULATING", "DISTRIBUTING", "NEUTRAL"] = "NEUTRAL"
        if net_24h > 0 and ex_signal == "BULLISH":
            signal = "ACCUMULATING"
        elif net_24h < 0 and ex_signal == "BEARISH":
            signal = "DISTRIBUTING"

        object.__setattr__(self, "smart_money_signal", signal)

        # Confidence derivation
        wallets = self.smart_money_flow_24h.unique_smart_wallets
        if wallets < 10:
            object.__setattr__(self, "confidence_modifier", 0.5)
        else:
            object.__setattr__(self, "confidence_modifier", 1.0)

        return self
