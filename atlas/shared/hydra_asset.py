"""Normalise ATLAS / exchange symbols to HYDRA Redis key suffixes.

HYDRA ClusterFuser and ingestors use bare bases (``BTC``, ``ETH``) under the
``hydra:`` prefix, not ``BTCUSDT`` or ``BTC-PERP``.
"""

from __future__ import annotations


def hydra_base_asset(symbol: str) -> str:
    """Return HYDRA-native base symbol for Redis keys (e.g. ``BTC``)."""
    upper = symbol.strip().upper()
    if "/" in upper:
        return upper.split("/", maxsplit=1)[0]
    if upper.endswith("-PERP"):
        return upper[: -len("-PERP")]
    if upper.endswith("USDT") and len(upper) > len("USDT"):
        return upper[: -len("USDT")]
    return upper
