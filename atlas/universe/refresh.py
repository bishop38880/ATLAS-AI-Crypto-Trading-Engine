"""Sync ``polaris:universe:all`` with optional CoinGecko community admission."""

from __future__ import annotations

import redis.asyncio as redis_asyncio
from loguru import logger

from atlas.core.asset_universe import ASSET_UNIVERSE, AssetConfig, polaris_universe_redis_members
from atlas.providers.coingecko.adapter import CoinGeckoAdapter
from atlas.shared.coingecko_symbol_map import COINGECKO_SIMPLE_PRICE_ID_BY_BASE
from atlas.shared.config import PolarisSettings
from atlas.universe.admission_filter import evaluate_community_admission

POLARIS_UNIVERSE_ALL_KEY = "polaris:universe:all"


def _compact_exchange_symbol(cfg: AssetConfig) -> str:
    """Return Redis member form (e.g. ``BTCUSDT``)."""
    return cfg.symbol.replace("/", "")


async def _admit_single_asset(
    adapter: CoinGeckoAdapter,
    cfg: AssetConfig,
    coingecko_slug: str,
) -> bool:
    """Fetch community enrichment and apply the cold-path admission gate."""
    base_asset = cfg.symbol.split("/", maxsplit=1)[0].strip().upper()
    try:
        envelope = await adapter.fetch_community_data(coingecko_slug)
    except Exception as exc:
        logger.warning(
            "universe_community_fetch_failed | asset={} | err={}",
            base_asset,
            exc,
        )
        return True
    return evaluate_community_admission(envelope)


async def collect_admitted_universe_members(
    adapter: CoinGeckoAdapter,
) -> tuple[str, ...]:
    """Build Redis members for assets passing the community admission gate."""
    admitted: list[str] = []
    for cfg in ASSET_UNIVERSE:
        base_asset = cfg.symbol.split("/", maxsplit=1)[0].strip().upper()
        slug = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(base_asset)
        if slug is None:
            logger.warning(
                "universe_community_skip_no_coingecko_id | asset={}",
                base_asset,
            )
            admitted.append(_compact_exchange_symbol(cfg))
            continue
        if await _admit_single_asset(adapter, cfg, slug):
            admitted.append(_compact_exchange_symbol(cfg))
    return tuple(admitted)


async def write_polaris_universe_all(
    redis_client: redis_asyncio.Redis,
    members: tuple[str, ...],
) -> None:
    """Replace the universe set atomically."""
    pipe = redis_client.pipeline()
    pipe.delete(POLARIS_UNIVERSE_ALL_KEY)
    if members:
        pipe.sadd(POLARIS_UNIVERSE_ALL_KEY, *members)
    await pipe.execute()


async def sync_polaris_universe_all_redis(
    redis_client: redis_asyncio.Redis,
    *,
    settings: PolarisSettings,
    adapter: CoinGeckoAdapter | None = None,
) -> int:
    """
    Align Redis ``polaris:universe:all`` with configured universe rows.

    When ``community_admission_gate_enabled`` is true and a CoinGecko adapter is
    supplied, members are filtered via ``evaluate_community_admission``.
    """
    if settings.community_admission_gate_enabled and adapter is not None:
        members = await collect_admitted_universe_members(adapter)
        mode = "community_gate"
    else:
        members = polaris_universe_redis_members()
        mode = "full_universe"

    await write_polaris_universe_all(redis_client, members)
    logger.info(
        "polaris_universe_all_synced | key={} | count={} | mode={}",
        POLARIS_UNIVERSE_ALL_KEY,
        len(members),
        mode,
    )
    return len(members)
