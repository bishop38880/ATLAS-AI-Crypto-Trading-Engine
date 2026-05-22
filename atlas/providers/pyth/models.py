"""Pyth Hermes data models — strict Decimal typing enforced.

All price and confidence values are computed from Pyth's
integer-plus-exponent representation via ``apply_pyth_exponent``.
No ``float()`` is used anywhere in this module.
"""

from decimal import Decimal

from pydantic import BaseModel


class PythPriceUpdate(BaseModel, frozen=True):
    """Immutable record of a single Pyth price update.

    Attributes:
        price_id: Pyth feed identifier (hex string without 0x prefix).
        price: Final price as Decimal (exponent already applied).
        conf: Confidence interval as Decimal (exponent already applied).
        publish_time: Unix timestamp of the price publication.
    """

    price_id: str
    price: Decimal
    conf: Decimal
    publish_time: float


def apply_pyth_exponent(value: str, expo: int) -> Decimal:
    """Convert a Pyth integer + exponent pair to a final Decimal.

    Pyth sends prices as an integer string with a separate exponent
    (e.g. price="6140993501000", expo=-8 → 61409.93501000).
    This function computes ``Decimal(value) * 10^expo`` safely
    using pure Decimal arithmetic.

    Args:
        value: The integer price or confidence as a string.
        expo: The exponent (typically negative, e.g. -8).

    Returns:
        The fully resolved Decimal price.
    """
    return Decimal(value) * Decimal(10) ** Decimal(expo)


# ─── PYTH FEED ID REGISTRY ──────────────────────────────────────────
# Maps ATLAS asset symbols to Pyth Hermes feed IDs.
# Source: https://docs.pyth.network/price-feeds/price-feeds
PYTH_FEED_IDS: dict[str, str] = {
    "BTC": "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43",
    "ETH": "ff61491a931112ddf1bd8147cd1b641375f79f5825126d665480874634fd0ace",
    "SOL": "ef0d8b6fda2ceba41da15d4095d1da392a0d2f8ed0c6c7bc0f4cfac8c280b56d",
    "BNB": "2f95862b045670cd22bee3114c39763a4a08beeb663b145d283c31d7d1101c4f",
    "AVAX": "93da3352f9f1d105fdfe4971cfa80e9dd777bfc5d0f683ebb6e1294b92137bb7",
    "DOGE": "dcef50dd0a4cd2dcc17e45df1676dcb336a11a61c69df7a0299b0150c672d25c",
    "MATIC": "5de33440f6c8ee339a76e3e5b0f5e089e0f74496cfdf75df1c58e9ef1aa0b7da",
    "ARB": "3fa4252848f9f0a1480be62745a4629d9eb1322aebab8a791e344b3b9c1adcf5",
    "LINK": "8ac0c70fff57e9aefdf5edf44b51d62c2d433653cbb2cf5cc06bb115af04d221",
    "SUI": "23d7315113f5b1d3ba7a83604c44b94d79f4fd69af77f804fc7f920a6dc65744",
}

# Reverse lookup: feed_id → ATLAS asset symbol
FEED_ID_TO_ASSET: dict[str, str] = {v: k for k, v in PYTH_FEED_IDS.items()}
