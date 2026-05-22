"""Shared real-time telemetry publisher for ATLAS."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import msgspec
import redis.asyncio as redis_asyncio

from atlas.shared.config import PolarisSettings

_lock = asyncio.Lock()
_publisher: redis_asyncio.Redis | None = None


async def _publisher_client() -> redis_asyncio.Redis:
    """Lazy singleton Redis client for telemetry publishes."""
    global _publisher
    async with _lock:
        if _publisher is None:
            settings = PolarisSettings()
            _publisher = redis_asyncio.from_url(settings.redis_url)
        return _publisher


async def broadcast_log(level: str, agent: str, message: str) -> None:
    """Publish a telemetry log to the Redis Pub/Sub channel."""
    redis_client = await _publisher_client()

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "agent": agent,
        "message": message,
    }
    payload = msgspec.json.encode(data)

    await asyncio.wait_for(
        redis_client.publish("atlas:telemetry:stream", payload),
        timeout=5.0,
    )
