"""Helius → agent scoring helpers for SOL/JUP."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from atlas.core.asset_universe import is_helius_enabled
from atlas.providers.helius.onchain_signals import normalize_solana_base
from atlas.scoring.confluence import OnChainSignals


def helius_onchain_from_context(context: dict[str, Any]) -> OnChainSignals | None:
    """Return cached Helius on-chain signals from pipeline context."""
    raw = context.get("helius_onchain")
    if isinstance(raw, OnChainSignals) and raw.data_source == "helius":
        return raw
    return None


def should_prefer_helius_exchange_flow(asset: str, helius: OnChainSignals) -> bool:
    """True when Helius flow tracker has usable exchange-flow data for this asset."""
    if not is_helius_enabled(asset):
        return False
    if helius.data_source != "helius":
        return False
    return (
        helius.exchange_netflow_4h != Decimal("0")
        or abs(helius.whale_outflow_zscore) >= 0.5
        or helius.active_addresses_zscore > 0.0
    )


def helius_exchange_netflow_decimal(helius: OnChainSignals) -> Decimal:
    """4h exchange netflow in USD (negative = outflow / bullish)."""
    return helius.exchange_netflow_4h


def helius_inflow_outflow_pair(helius: OnChainSignals) -> tuple[Decimal, Decimal]:
    """Derive inflow/outflow USD from 4h netflow for pressure scoring."""
    net = helius.exchange_netflow_4h
    if net > Decimal("0"):
        return net, Decimal("0")
    if net < Decimal("0"):
        return Decimal("0"), abs(net)
    return Decimal("0"), Decimal("0")


def asset_base_for_helius(asset: str, context: dict[str, Any]) -> str:
    """Resolve base ticker from data payload or context."""
    raw = str(context.get("asset") or asset or "")
    return normalize_solana_base(raw)
