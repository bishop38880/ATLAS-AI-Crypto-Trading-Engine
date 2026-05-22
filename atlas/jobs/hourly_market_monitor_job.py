"""Background hourly market monitoring loop."""

from __future__ import annotations

import asyncio
import random

import asyncpg
import httpx
import redis.asyncio as redis_asyncio
from loguru import logger

from atlas.monitoring.pipeline import run_hourly_monitor_cycle
from atlas.shared.config import PolarisSettings

_LOCK_KEY = "polaris:job:hourly_market_monitor:lock"
_LOCK_TTL_SECONDS = 3300


async def run_hourly_market_monitor_once(
    redis_client: redis_asyncio.Redis,
    *,
    pg_pool: asyncpg.Pool | None,
    settings: PolarisSettings,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Execute a single cycle under a Redis distributed lock."""
    acquired = await redis_client.set(_LOCK_KEY, "1", nx=True, ex=_LOCK_TTL_SECONDS)
    if not acquired:
        logger.info("hourly_market_monitor_lock_held")
        return
    try:
        await run_hourly_monitor_cycle(
            settings=settings,
            redis_client=redis_client,
            pg_pool=pg_pool,
            http_client=http_client,
        )
    finally:
        await redis_client.delete(_LOCK_KEY)


async def hourly_market_monitor_loop(
    redis_client: redis_asyncio.Redis,
    *,
    pg_pool: asyncpg.Pool | None,
    settings: PolarisSettings,
) -> None:
    """Jittered infinite loop until cancelled."""
    interval = settings.hourly_monitor_interval_seconds
    jitter = max(30, min(300, interval // 10))
    logger.info("hourly_market_monitor_loop_started | interval_s={}", interval)
    while True:
        try:
            await run_hourly_market_monitor_once(redis_client, pg_pool=pg_pool, settings=settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("hourly_market_monitor_tick_failed | err={}", exc)
        sleep_s = interval + random.uniform(-jitter, jitter)
        await asyncio.sleep(max(60.0, sleep_s))
