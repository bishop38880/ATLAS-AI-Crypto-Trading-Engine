"""Map Helius Solana flow tracker output into confluence ``OnChainSignals``."""

from __future__ import annotations

from decimal import Decimal

from atlas.core.asset_universe import is_helius_enabled
from atlas.providers.helius.models import FlowSignals
from atlas.providers.helius.solana_addresses import HELIUS_ENABLED_SYMBOLS
from atlas.scoring.confluence import OnChainSignals
from atlas.services import solana_flow_tracker


def normalize_solana_base(asset: str) -> str:
    """Normalize ``SOL/USDT``, ``SOL-USDT``, or ``SOL`` to base ticker."""
    symbol = asset.strip().upper()
    if "/" in symbol:
        return symbol.split("/", 1)[0]
    if "-" in symbol:
        return symbol.split("-", 1)[0]
    return symbol


def calculate_whale_outflow_zscore(netflow_24h_usd: float) -> float:
    """Proxy z-score from 24h exchange netflow (negative = outflow / bullish)."""
    if netflow_24h_usd < -1_000_000:
        return min(3.0, abs(netflow_24h_usd) / 1_000_000)
    if netflow_24h_usd > 1_000_000:
        return -min(3.0, netflow_24h_usd / 1_000_000)
    return netflow_24h_usd / 2_000_000


def calculate_active_addresses_zscore(whale_tx_count_24h: int) -> float:
    """Proxy activity z-score from whale-tier transaction count."""
    if whale_tx_count_24h <= 0:
        return 0.0
    return min(3.0, whale_tx_count_24h / 7.0)


def flow_signals_to_onchain(flow: FlowSignals) -> OnChainSignals:
    """Convert rolling Helius flow windows into confluence on-chain inputs."""
    net_4h = Decimal(str(round(flow.exchange_netflow_4h, 2)))
    net_24h = flow.exchange_netflow_24h
    whale_inflow = Decimal("0")
    if net_4h > Decimal("0"):
        whale_inflow = net_4h

    return OnChainSignals(
        whale_outflow_zscore=calculate_whale_outflow_zscore(net_24h),
        whale_inflow_usd=whale_inflow,
        exchange_netflow_4h=net_4h,
        active_addresses_zscore=calculate_active_addresses_zscore(flow.whale_tx_count_24h),
        data_source="helius",
    )


def helius_flow_is_actionable(onchain: OnChainSignals) -> bool:
    """True when Helius rolling windows contain non-trivial flow data."""
    if onchain.data_source != "helius":
        return False
    return (
        onchain.exchange_netflow_4h != Decimal("0")
        or onchain.active_addresses_zscore > 0.0
        or abs(onchain.whale_outflow_zscore) > 0.05
    )


async def fetch_helius_onchain_signals(asset: str) -> OnChainSignals | None:
    """Load ``OnChainSignals`` from Redis flow tracker when asset is SOL/JUP."""
    if not is_helius_enabled(asset):
        return None
    base = normalize_solana_base(asset)
    if base not in HELIUS_ENABLED_SYMBOLS:
        return None
    flow = await solana_flow_tracker.get_signals(base)
    if flow is None:
        return None
    mapped = flow_signals_to_onchain(flow)
    if not helius_flow_is_actionable(mapped):
        return mapped
    return mapped
