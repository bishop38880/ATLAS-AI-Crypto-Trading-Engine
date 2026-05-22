"""Cross-source spot price helpers for Validation Gate consistency.

Pairs Pyth Hermes prices in Redis with CoinGecko ``/simple/price`` references
(60s TTL) using :class:`ConsistencyChecker` thresholds (0.5% for ``spot_price``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.core.consistency_checker import ConsistencyChecker, ConsistencyResult


async def read_pyth_spot_price_usd(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    polaris_base: str,
) -> Decimal | None:
    """Read the latest Pyth USD price from ``atlas:price:{BASE}``."""
    key = "atlas:price:{}".format(polaris_base.strip().upper())
    try:
        raw = await redis_client.get(key)
    except Exception as exc:
        logger.warning("pyth_redis_read_failed | base={} | err={}", polaris_base, exc)
        return None
    if raw is None:
        return None
    try:
        payload: Any = msgspec.json.decode(
            raw if isinstance(raw, (bytes, bytearray)) else raw.encode("utf-8"),
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    price_raw = payload.get("price")
    if price_raw is None:
        return None
    try:
        return Decimal(str(price_raw))
    except Exception:
        return None


async def spot_price_consistency_pyth_coingecko(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    polaris_base: str,
    coingecko_price_usd: Decimal,
    checker: ConsistencyChecker,
) -> ConsistencyResult | None:
    """Return a consistency result when both Pyth and CoinGecko prices exist."""
    if coingecko_price_usd <= 0:
        return None
    pyth_px = await read_pyth_spot_price_usd(redis_client, polaris_base)
    if pyth_px is None or pyth_px <= 0:
        return None
    return await checker.check_consistency(
        "spot_price",
        {"pyth_hermes": pyth_px, "coingecko": coingecko_price_usd},
    )
