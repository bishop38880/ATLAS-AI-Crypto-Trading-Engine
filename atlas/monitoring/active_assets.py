"""Resolve the 8-asset daily rotation set for hourly monitoring."""

from __future__ import annotations

import redis.asyncio as redis_async
from loguru import logger

from atlas.core.asset_universe import ASSET_UNIVERSE, AssetTier

_DAILY_8_KEY = "polaris:rotation:daily_8"
_FALLBACK_COUNT = 8


def _decode_member(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode()
    return raw


def _normalize_to_base(symbol: str) -> str:
    cleaned = symbol.upper().strip().replace("/", "")
    if cleaned.endswith("USDT") and len(cleaned) > 4:
        return cleaned[:-4]
    return cleaned


def fallback_daily_8_bases() -> list[str]:
    """First eight ALWAYS_ON perpetual bases when Redis rotation is empty."""
    bases: list[str] = []
    for row in ASSET_UNIVERSE:
        if row.tier != AssetTier.ALWAYS_ON:
            continue
        bases.append(_normalize_to_base(row.symbol))
        if len(bases) >= _FALLBACK_COUNT:
            break
    return bases


async def resolve_daily_8_asset_bases(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
) -> list[str]:
    """Read ``polaris:rotation:daily_8`` or fall back to ALWAYS_ON majors."""
    try:
        members = await redis_client.smembers(_DAILY_8_KEY)
    except Exception as exc:
        logger.warning("monitoring_daily_8_redis_failed | err={}", exc)
        return fallback_daily_8_bases()

    if not members:
        return fallback_daily_8_bases()

    bases = sorted({_normalize_to_base(_decode_member(m)) for m in members})
    return bases[:_FALLBACK_COUNT]
