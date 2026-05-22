"""Redis-backed shadow metrics fetch for MARL deliberation."""

from __future__ import annotations

import asyncio
from typing import Protocol

import redis.asyncio as redis_async

from atlas.marl.message_encoder import ShadowMetrics


_METRIC_KEYS: tuple[str, str] = (
    "avg_pairwise_correlation",
    "volatility_zscore",
)


class ShadowInjector:
    """Loads shadow-only context metrics from Redis."""

    def __init__(self, redis_client: "redis_async.Redis | _RedisLike") -> None:
        self._redis = redis_client

    async def fetch(self, asset: str) -> ShadowMetrics:
        redis_keys = [f"atlas:marl:shadow:{asset}:{key}" for key in _METRIC_KEYS]
        values = await self._redis.mget(*redis_keys)
        correlation = self._as_float(values[0], default=0.0)
        volatility = self._as_float(values[1], default=0.0)
        return ShadowMetrics(
            avg_pairwise_correlation=correlation,
            volatility_zscore=volatility,
        )

    def _as_float(self, raw: bytes | str | None, default: float) -> float:
        if raw is None:
            return default
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        try:
            return float(text)
        except (TypeError, ValueError):
            return default


class _RedisLike(Protocol):
    async def get(self, key: str) -> bytes | str | None: ...
    async def mget(self, *keys: str) -> list[bytes | str | None]: ...
