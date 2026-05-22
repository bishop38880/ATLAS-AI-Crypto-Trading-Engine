"""Asset Universe configuration and filtering."""

from enum import Enum

from pydantic import BaseModel


class AssetTier(str, Enum):
    """Tier of the asset determining its monitoring priority."""

    ALWAYS_ON = "ALWAYS_ON"
    ROTATION = "ROTATION"


class AssetConfig(BaseModel, frozen=True):
    """Configuration for a specific asset in the universe.

    Attributes:
        symbol: The trading pair symbol (e.g., 'BTC/USDT').
        tier: The monitoring tier (ALWAYS_ON or ROTATION).
        group: The correlation group (e.g., 'btc', 'eth_l1', 'alt').
        has_perp: Whether the asset has a perpetual futures market.
        helius_enabled: Solana on-chain flow via Helius (SOL, JUP only).
    """

    symbol: str
    tier: AssetTier
    group: str
    has_perp: bool = True
    helius_enabled: bool = False


# CoinGecko USD market-cap rank (excluding stablecoins / FX-pegged shells),
# intersected with Binance USDⓈ-M perpetual listings — snapshot May 2026.
# The first 18 slots stay ALWAYS_ON (≈ top liquid perpetual majors through SUI).
# Trailing rows XAG/XAU/XPT/XPD: ISO precious-metal bases (USDT-quoted; venue coverage varies).
ASSET_UNIVERSE: list[AssetConfig] = [
    AssetConfig(symbol="BTC/USDT", tier=AssetTier.ALWAYS_ON, group="btc"),
    AssetConfig(symbol="ETH/USDT", tier=AssetTier.ALWAYS_ON, group="eth_l1"),
    AssetConfig(symbol="BNB/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="XRP/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="SOL/USDT", tier=AssetTier.ALWAYS_ON, group="alt", helius_enabled=True),
    AssetConfig(symbol="TRX/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="DOGE/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="HYPE/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="ADA/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="ZEC/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="BCH/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="LINK/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="XMR/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="CC/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="TON/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="XLM/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="LTC/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="SUI/USDT", tier=AssetTier.ALWAYS_ON, group="alt"),
    AssetConfig(symbol="AVAX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="HBAR/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="TAO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="XAUT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="UNI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="DOT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="PAXG/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="WLFI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="NEAR/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ONDO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ASTER/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SKY/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ICP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ETC/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AAVE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="MORPHO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="QNT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ENA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ALGO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ATOM/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="KAS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="POL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="RENDER/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="WLD/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="STABLE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="APT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="FIL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="JST/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ARB/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="JUP/USDT", tier=AssetTier.ROTATION, group="alt", helius_enabled=True),
    AssetConfig(symbol="PUMP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="VVV/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="DEXE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="VET/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="UB/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="DASH/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="PENGU/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="TRUMP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="NIGHT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="VIRTUAL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CAKE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="INJ/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="BILL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="KITE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="STX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="FET/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CHZ/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="EDGE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SEI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AERO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="XTZ/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="TIA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CRV/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SIREN/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SUN/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SPX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CFX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ETHFI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="PENDLE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="MON/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SKYAI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ZRO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GWEI/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="BSV/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="LDO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="JASMY/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="LAB/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="OP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GRT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ENS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="KAIA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="PYTH/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="PIEVERSE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="IOTA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="STRK/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SYRUP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="XPL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="LIT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="JTO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AKT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="COMP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="THETA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="NEO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="FF/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AXS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="TWT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="WIF/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="FARTCOIN/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SAND/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GRASS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="RUNE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="IP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="MANA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="WAL/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GALA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="BEAT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ZK/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="IMX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CVX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GENIUS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="RAVE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CFG/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="BAT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="EIGEN/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SFP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="RIVER/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AR/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="APE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="GLM/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="TAG/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="FLUID/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="EGLD/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ATH/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="DYDX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SAHARA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="BANANAS31/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="RSR/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="IRYS/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="CHIP/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SENT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="ZEN/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="MEGA/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="SNX/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="KAITO/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="AWE/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="LPT/USDT", tier=AssetTier.ROTATION, group="alt"),
    AssetConfig(symbol="XAG/USDT", tier=AssetTier.ROTATION, group="precious_metal"),
    AssetConfig(symbol="XAU/USDT", tier=AssetTier.ROTATION, group="precious_metal"),
    AssetConfig(symbol="XPT/USDT", tier=AssetTier.ROTATION, group="precious_metal"),
    AssetConfig(symbol="XPD/USDT", tier=AssetTier.ROTATION, group="precious_metal"),
]


def polaris_universe_redis_members() -> tuple[str, ...]:
    """Symbols for ``polaris:universe:all`` — compact exchange form (e.g. ``BTCUSDT``)."""
    return tuple(cfg.symbol.replace("/", "") for cfg in ASSET_UNIVERSE)


def get_active_assets() -> list[AssetConfig]:
    """Filter and return assets that support perpetuals.

    Returns:
        List of active AssetConfig objects.
    """
    return [asset for asset in ASSET_UNIVERSE if asset.has_perp]


def normalize_pair_base(asset: str) -> str:
    """Normalize ``BTC/USDT`` or ``BTC-USDT`` to ``BTC``."""
    symbol = asset.strip().upper()
    if "/" in symbol:
        return symbol.split("/", 1)[0]
    if "-" in symbol:
        return symbol.split("-", 1)[0]
    return symbol


def is_helius_enabled(asset: str) -> bool:
    """True when the pair is configured for Helius Solana on-chain flow (SOL, JUP)."""
    base = normalize_pair_base(asset)
    for cfg in ASSET_UNIVERSE:
        if normalize_pair_base(cfg.symbol) == base:
            return cfg.helius_enabled
    return False
