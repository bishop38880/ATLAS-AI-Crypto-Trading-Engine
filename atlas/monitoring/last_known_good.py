"""Redis last-known-good cache for normalized hourly quotes."""

from __future__ import annotations

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.monitoring.models import NormalizedHourlyQuote

_LKG_PREFIX = "polaris:monitoring:lkg:"


def last_known_good_key(asset_base: str) -> str:
    return f"{_LKG_PREFIX}{asset_base.strip().upper()}"


async def read_last_known_good(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    asset_base: str,
) -> NormalizedHourlyQuote | None:
    """Return cached quote or ``None`` when missing or corrupt."""
    raw = await redis_client.get(last_known_good_key(asset_base))
    if raw is None:
        return None
    try:
        payload = msgspec.json.decode(raw if isinstance(raw, (bytes, bytearray)) else raw.encode())
        return NormalizedHourlyQuote.model_validate(payload)
    except Exception as exc:
        logger.warning("monitoring_lkg_decode_failed | asset={} | err={}", asset_base, exc)
        return None


async def write_last_known_good(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    quote: NormalizedHourlyQuote,
    ttl_seconds: int,
) -> None:
    """Persist a healthy quote as the failover cache."""
    encoded = msgspec.json.encode(quote.model_dump(mode="json"))
    await redis_client.setex(last_known_good_key(quote.asset_base), ttl_seconds, encoded)
