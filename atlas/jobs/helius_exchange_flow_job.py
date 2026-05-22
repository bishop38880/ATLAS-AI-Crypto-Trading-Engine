"""Background Helius exchange-flow polling (webhook fallback / reconciliation)."""

from __future__ import annotations

import asyncio

import redis.asyncio as redis_asyncio
from loguru import logger

from atlas.core.registry import helius_provider, initialize_registry
from atlas.providers.helius.solana_addresses import HELIUS_ENABLED_SYMBOLS
from atlas.shared.config import PolarisSettings

_LOCK_KEY = "polaris:job:helius_flow_poll:lock"


async def run_helius_exchange_flow_poll_once(
    redis_client: redis_asyncio.Redis,
    settings: PolarisSettings,
) -> None:
    """Poll exchange addresses once per symbol under a short Redis lock."""
    acquired = await redis_client.set(_LOCK_KEY, "1", nx=True, ex=120)
    if not acquired:
        return

    try:
        if helius_provider is None:
            initialize_registry(settings, redis_client)
        provider = helius_provider
        if provider is None or not provider.is_configured:
            logger.debug("helius_flow_poll_skipped | reason=not_configured")
            return

        total = 0
        for symbol in sorted(HELIUS_ENABLED_SYMBOLS):
            try:
                count = await provider.poll_exchange_flows(symbol)
                total += count
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "helius_flow_poll_symbol_failed | symbol={} | err={}",
                    symbol,
                    exc,
                )

        if total > 0:
            logger.info("helius_flow_poll_done | new_events={}", total)
    finally:
        await redis_client.delete(_LOCK_KEY)


async def helius_exchange_flow_poll_loop(
    redis_client: redis_asyncio.Redis,
    settings: PolarisSettings,
) -> None:
    """Jittered loop until cancelled."""
    interval = float(settings.helius_flow_poll_interval_seconds)
    logger.info("helius_flow_poll_loop_started | interval_s={}", interval)
    while True:
        try:
            await run_helius_exchange_flow_poll_once(redis_client, settings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("helius_flow_poll_tick_failed | err={}", exc)
        await asyncio.sleep(max(60.0, interval))
