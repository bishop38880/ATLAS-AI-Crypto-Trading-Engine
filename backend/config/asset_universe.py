"""Canonical POLARIS dashboard (33-slot) ladder and cross-venue identifiers.

Single source for:
- Fallback ``BASE/USDT`` ladder bases when Redis ``polaris:rotation:active_33`` is cold
- ALWAYS_ON tier (tier-one majors) aligned with atlas ``AssetTier.ALWAYS_ON``
- Bitget / Kraken Futures / CoinGecko identifiers for downstream MCP + providers

Venue semantics (explicit so migrations do not guess):
- ``symbol_bitget_usdt``: Bitget mixed USDT-margined perpetual, ``{BASE}USDT`` (Polaris casing).
- ``symbol_kraken_futures``: Kraken Derivatives perpetual / fixed-expiry ticker (``PI_*`` /
  ``PF_*``). Empty string when no reliable Kraken listing is assumed.
- ``symbol_coinglass``: CoinGlass **coin ticker** accepted by aggregates / liquidation APIs
  (typically ``BASE``, not ``BASEUSDT``).
- ``coingecko_id``: Coin ``/coins/{id}`` slug used by ``simple/price`` and site URLs.

Invariant: ladder order mirrors the first thirty-three rows of ``atlas.core.asset_universe``.
``ASSET_UNIVERSE`` (148 rows: top-144 crypto perpetuals + XAG/XAU/XPT/XPD) —
eighteen ALWAYS_ON, then fifteen ROTATION in the dashboard prefix; metals trail the extended list.
"""

from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from pydantic import BaseModel, Field

POLARIS_DASHBOARD_CARD_COUNT: int = 33


class PolarisDashboardTier(str, Enum):
    """Dashboard-only tier naming (matches ``atlas.core.asset_universe.AssetTier`` values)."""

    ALWAYS_ON = "ALWAYS_ON"
    ROTATION = "ROTATION"


class PolarisDashboardAsset(BaseModel, frozen=True):
    polaris_base: str = Field(
        description="Polaris perpetual base ticker (BTC, ETH, …) — quote is always USDT.",
    )
    tier: PolarisDashboardTier
    symbol_bitget_usdt: str = Field(description="Bitget USDT perpetual (e.g. BTCUSDT).")
    symbol_kraken_futures: str = Field(
        description="Kraken Derivatives ticker (empty if none / unknown).",
    )
    symbol_coinglass: str = Field(
        description="CoinGlass coin ticker (aggregated derivatives stats).",
    )
    coingecko_id: str = Field(description="CoinGecko API coin id slug.")


# Kraken perp labels we intentionally leave blank until execution wiring verifies availability.
_NO_KRAKEN_PERP_ASSUMED: frozenset[str] = frozenset({"CC", "HYPE", "WLFI", "ASTER", "SKY"})


def derive_kraken_futures_placeholder(base_upper: str) -> str:
    """Return Kraken Derivatives ticker using PI/PF conventions."""
    upper = base_upper.strip().upper()
    if upper in _NO_KRAKEN_PERP_ASSUMED:
        return ""
    if upper == "BTC":
        return "PI_XBTUSD"
    return f"PF_{upper}USD"


def derive_bitget_usdt_symbol(polaris_base_upper: str) -> str:
    """``BASE`` → ``BASEUSDT`` for Bitget USDT perpetuals."""
    return f"{polaris_base_upper.strip().upper()}USDT"


def derive_coinglass_coin_ticker(polaris_base_upper: str) -> str:
    """CoinGlass coin-level aggregates use the bare ticker (not ``BASEUSDT``)."""
    return polaris_base_upper.strip().upper()


def _row(base: str, tier: PolarisDashboardTier, *, coingecko_id: str, kraken: str | None) -> PolarisDashboardAsset:
    polaris_base_upper = base.strip().upper()
    ks = derive_kraken_futures_placeholder(polaris_base_upper) if kraken is None else kraken
    return PolarisDashboardAsset(
        polaris_base=polaris_base_upper,
        tier=tier,
        symbol_bitget_usdt=derive_bitget_usdt_symbol(polaris_base_upper),
        symbol_kraken_futures=ks,
        symbol_coinglass=derive_coinglass_coin_ticker(polaris_base_upper),
        coingecko_id=coingecko_id,
    )


POLARIS_DASHBOARD_ASSETS: tuple[PolarisDashboardAsset, ...] = (
    _row("BTC", PolarisDashboardTier.ALWAYS_ON, coingecko_id="bitcoin", kraken=None),
    _row("ETH", PolarisDashboardTier.ALWAYS_ON, coingecko_id="ethereum", kraken=None),
    _row("BNB", PolarisDashboardTier.ALWAYS_ON, coingecko_id="binancecoin", kraken=None),
    _row("XRP", PolarisDashboardTier.ALWAYS_ON, coingecko_id="ripple", kraken=None),
    _row("SOL", PolarisDashboardTier.ALWAYS_ON, coingecko_id="solana", kraken=None),
    _row("TRX", PolarisDashboardTier.ALWAYS_ON, coingecko_id="tron", kraken=None),
    _row("DOGE", PolarisDashboardTier.ALWAYS_ON, coingecko_id="dogecoin", kraken=None),
    _row("HYPE", PolarisDashboardTier.ALWAYS_ON, coingecko_id="hyperliquid", kraken=None),
    _row("ADA", PolarisDashboardTier.ALWAYS_ON, coingecko_id="cardano", kraken=None),
    _row("ZEC", PolarisDashboardTier.ALWAYS_ON, coingecko_id="zcash", kraken=None),
    _row("BCH", PolarisDashboardTier.ALWAYS_ON, coingecko_id="bitcoin-cash", kraken=None),
    _row("LINK", PolarisDashboardTier.ALWAYS_ON, coingecko_id="chainlink", kraken=None),
    _row("XMR", PolarisDashboardTier.ALWAYS_ON, coingecko_id="monero", kraken=None),
    _row("CC", PolarisDashboardTier.ALWAYS_ON, coingecko_id="canton", kraken=None),
    _row("TON", PolarisDashboardTier.ALWAYS_ON, coingecko_id="the-open-network", kraken=None),
    _row("XLM", PolarisDashboardTier.ALWAYS_ON, coingecko_id="stellar", kraken=None),
    _row("LTC", PolarisDashboardTier.ALWAYS_ON, coingecko_id="litecoin", kraken=None),
    _row("SUI", PolarisDashboardTier.ALWAYS_ON, coingecko_id="sui", kraken=None),
    _row("AVAX", PolarisDashboardTier.ROTATION, coingecko_id="avalanche-2", kraken=None),
    _row("HBAR", PolarisDashboardTier.ROTATION, coingecko_id="hedera-hashgraph", kraken=None),
    _row("TAO", PolarisDashboardTier.ROTATION, coingecko_id="bittensor", kraken=None),
    _row("XAUT", PolarisDashboardTier.ROTATION, coingecko_id="tether-gold", kraken=None),
    _row("UNI", PolarisDashboardTier.ROTATION, coingecko_id="uniswap", kraken=None),
    _row("DOT", PolarisDashboardTier.ROTATION, coingecko_id="polkadot", kraken=None),
    _row("PAXG", PolarisDashboardTier.ROTATION, coingecko_id="pax-gold", kraken=None),
    _row("WLFI", PolarisDashboardTier.ROTATION, coingecko_id="world-liberty-financial", kraken=None),
    _row("NEAR", PolarisDashboardTier.ROTATION, coingecko_id="near", kraken=None),
    _row("ONDO", PolarisDashboardTier.ROTATION, coingecko_id="ondo-finance", kraken=None),
    _row("ASTER", PolarisDashboardTier.ROTATION, coingecko_id="aster-2", kraken=None),
    _row("SKY", PolarisDashboardTier.ROTATION, coingecko_id="sky", kraken=None),
    _row("ICP", PolarisDashboardTier.ROTATION, coingecko_id="internet-computer", kraken=None),
    _row("ETC", PolarisDashboardTier.ROTATION, coingecko_id="ethereum-classic", kraken=None),
    _row("AAVE", PolarisDashboardTier.ROTATION, coingecko_id="aave", kraken=None),
)


def polaris_dashboard_bases_fallback_order() -> tuple[str, ...]:
    """Bases appended after Redis ladder runs dry — same order as atlas ASSET_UNIVERSE[:33]."""
    return tuple(row.polaris_base for row in POLARIS_DASHBOARD_ASSETS)


def polaris_tier_one_bases_fallback_order() -> tuple[str, ...]:
    """ALWAYS_ON subset (tier-one filter) preserving dashboard ordering."""
    return tuple(row.polaris_base for row in POLARIS_DASHBOARD_ASSETS if row.tier == PolarisDashboardTier.ALWAYS_ON)


_COINGECKO_BY_BASE: dict[str, str] = {
    asset.polaris_base: asset.coingecko_id for asset in POLARIS_DASHBOARD_ASSETS
}


POLARIS_COINGECKO_ID_BY_BASE: MappingProxyType[str, str] = MappingProxyType(_COINGECKO_BY_BASE)


POLARIS_DASHBOARD_FALLBACK_BASES_ORDER: tuple[str, ...] = polaris_dashboard_bases_fallback_order()
POLARIS_DASHBOARD_TIER_ONE_ORDER: tuple[str, ...] = polaris_tier_one_bases_fallback_order()
POLARIS_DASHBOARD_VALID_BASES: frozenset[str] = frozenset(POLARIS_DASHBOARD_FALLBACK_BASES_ORDER)


def polaris_dashboard_coingecko_id(base: str) -> str | None:
    """Return CoinGecko id for a Polaris base or ``None`` if outside the canonical ladder."""
    return _COINGECKO_BY_BASE.get(base.strip().upper())
