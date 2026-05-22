"""Pyth Hermes cross-check source (Redis hot cache, no per-poll HTTP)."""

from __future__ import annotations

import time
from decimal import Decimal

import redis.asyncio as redis_async
from loguru import logger

from atlas.core.cross_source_spot_price import read_pyth_spot_price_usd
from atlas.core.circuit_breaker import get_circuit_breaker
from atlas.monitoring.metrics import record_fetch_outcome


class PythCachedHourlySource:
    """Read the latest Pyth USD price from ``atlas:price:{BASE}``."""

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client
        self._breaker = get_circuit_breaker("pyth:hermes")

    async def fetch_spot(self, asset_base: str) -> tuple[Decimal | None, float, str]:
        """Return ``(price_or_none, latency_ms, status)``."""
        started = time.perf_counter()
        status = "failed"

        async def _read() -> Decimal | None:
            return await read_pyth_spot_price_usd(self._redis, asset_base)

        price: Decimal | None = None
        try:
            price = await self._breaker.call(_read, is_critical=False)
            status = "ok" if price is not None and price > 0 else "degraded"
        except Exception as exc:
            logger.warning("monitoring_pyth_read_failed | asset={} | err={}", asset_base, exc)
        latency_ms = (time.perf_counter() - started) * 1000.0
        await record_fetch_outcome(
            self._redis,
            provider="pyth",
            key_id="redis_cache",
            success=status == "ok",
            latency_ms=latency_ms,
        )
        return price, latency_ms, status
