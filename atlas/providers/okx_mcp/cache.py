"""OKX MCP Redis caching layer.

Read/write functions for all OKX MCP data types.
Uses redis.asyncio. Never creates new connections — pool injected.
"""

from typing import Any

import redis.asyncio as redis_async
from loguru import logger
import msgspec

from atlas.providers.okx_mcp.models import (
    OKXFundingHistory,
    OKXFundingRate,
    OKXLiquidationSnapshot,
    OKXLongShortRatio,
    OKXOpenInterest,
    OKXOpenInterestHistory,
)

TTL_FUNDING_RATE = 10
TTL_FUNDING_HISTORY = 300
TTL_OPEN_INTEREST = 10
TTL_OI_HISTORY = 300
TTL_LONG_SHORT_RATIO = 30
TTL_LIQUIDATIONS = 5
_PREFIX = "provider:okx_mcp"


def _key(asset: str, data_type: str) -> str:
    """Build Redis cache key."""
    return f"{_PREFIX}:{asset}:{data_type}"


async def read_funding_rate(
    r: redis_async.Redis, asset: str,
) -> OKXFundingRate | None:
    """Read cached funding rate."""
    raw = await r.get(_key(asset, "funding_rate"))
    if not raw:
        return None
    try:
        return OKXFundingRate.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_funding_rate(
    r: redis_async.Redis, asset: str, data: OKXFundingRate,
) -> None:
    """Write funding rate to cache."""
    await r.setex(
        _key(asset, "funding_rate"), TTL_FUNDING_RATE, msgspec.json.encode(data.model_dump(mode="json")),
    )


async def read_funding_history(
    r: redis_async.Redis, asset: str,
) -> OKXFundingHistory | None:
    """Read cached funding history."""
    raw = await r.get(_key(asset, "funding_history"))
    if not raw:
        return None
    try:
        return OKXFundingHistory.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_funding_history(
    r: redis_async.Redis, asset: str, data: OKXFundingHistory,
) -> None:
    """Write funding history to cache."""
    await r.setex(
        _key(asset, "funding_history"), TTL_FUNDING_HISTORY,
        msgspec.json.encode(data.model_dump(mode="json")),
    )


async def read_open_interest(
    r: redis_async.Redis, asset: str,
) -> OKXOpenInterest | None:
    """Read cached open interest."""
    raw = await r.get(_key(asset, "open_interest"))
    if not raw:
        return None
    try:
        return OKXOpenInterest.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_open_interest(
    r: redis_async.Redis, asset: str, data: OKXOpenInterest,
) -> None:
    """Write open interest to cache."""
    await r.setex(
        _key(asset, "open_interest"), TTL_OPEN_INTEREST,
        msgspec.json.encode(data.model_dump(mode="json")),
    )


async def read_oi_history(
    r: redis_async.Redis, asset: str,
) -> OKXOpenInterestHistory | None:
    """Read cached OI history."""
    raw = await r.get(_key(asset, "oi_history"))
    if not raw:
        return None
    try:
        return OKXOpenInterestHistory.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_oi_history(
    r: redis_async.Redis, asset: str, data: OKXOpenInterestHistory,
) -> None:
    """Write OI history to cache."""
    await r.setex(
        _key(asset, "oi_history"), TTL_OI_HISTORY, msgspec.json.encode(data.model_dump(mode="json")),
    )


async def read_long_short_ratio(
    r: redis_async.Redis, asset: str,
) -> OKXLongShortRatio | None:
    """Read cached L/S ratio."""
    raw = await r.get(_key(asset, "long_short_ratio"))
    if not raw:
        return None
    try:
        return OKXLongShortRatio.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_long_short_ratio(
    r: redis_async.Redis, asset: str, data: OKXLongShortRatio,
) -> None:
    """Write L/S ratio to cache."""
    await r.setex(
        _key(asset, "long_short_ratio"), TTL_LONG_SHORT_RATIO,
        msgspec.json.encode(data.model_dump(mode="json")),
    )


async def read_liquidations(
    r: redis_async.Redis, asset: str,
) -> OKXLiquidationSnapshot | None:
    """Read cached liquidation snapshot."""
    raw = await r.get(_key(asset, "liquidations"))
    if not raw:
        return None
    try:
        return OKXLiquidationSnapshot.model_validate(msgspec.json.decode(raw))
    except Exception as e:
        logger.error("okx_mcp cache | asset={} | err={}", asset, e)
        return None


async def write_liquidations(
    r: redis_async.Redis, asset: str, data: OKXLiquidationSnapshot,
) -> None:
    """Write liquidation snapshot to cache."""
    await r.setex(
        _key(asset, "liquidations"), TTL_LIQUIDATIONS,
        msgspec.json.encode(data.model_dump(mode="json")),
    )


async def write_health(
    r: redis_async.Redis, health: dict[str, Any],
) -> None:
    """Write connector health to Redis (no TTL)."""
    import msgspec
    await r.set("agent:okx_mcp:status", msgspec.json.encode(health))
