"""Persist CoinGecko community fundamentals snapshots (cold path)."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Callable

import asyncpg
import redis.asyncio as redis_asyncio
from loguru import logger

from atlas.shared.config import PolarisSettings

from atlas.core.asset_universe import ASSET_UNIVERSE
from atlas.providers.coingecko.adapter import CoinGeckoAdapter
from atlas.providers.coingecko.models import CommunityData
from atlas.shared.coingecko_symbol_map import COINGECKO_SIMPLE_PRICE_ID_BY_BASE

COMMUNITY_SNAPSHOT_LOCK_KEY = "polaris:job:community_snapshot:lock"
COMMUNITY_SNAPSHOT_LOCK_TTL_SECONDS = 3600


def default_universe_coingecko_pairs() -> list[tuple[str, str]]:
    """
    Build ``(POLARIS_BASE, COINGECKO_SLUG)`` pairs sourced from atlas universe wiring.

    Skips perpetual listings without curated CoinGecko ids so callers never invent
    slugs implicitly.
    """
    pairs: list[tuple[str, str]] = []
    for universe_row in ASSET_UNIVERSE:
        base_asset = universe_row.symbol.split("/", maxsplit=1)[0].strip().upper()
        slug = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(base_asset)
        if slug is None:
            logger.warning(
                "community_snapshot_unknown_coingecko_id | asset={}",
                base_asset,
            )
            continue
        pairs.append((base_asset, slug))
    return pairs


async def _persist_community_snapshot(
    conn: asyncpg.Connection,
    base_ticker: str,
    snapshot: CommunityData,
) -> None:
    """Insert a fundamentals row keyed by Polaris perpetual base ticker."""
    await conn.execute(
        """
        INSERT INTO asset_community_history (
            asset,
            twitter_followers,
            reddit_subscribers,
            reddit_avg_posts_48h,
            reddit_avg_comments_48h,
            reddit_active_48h,
            telegram_users
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7
        )
        """,
        base_ticker,
        snapshot.twitter_followers,
        snapshot.reddit_subscribers,
        snapshot.reddit_average_posts_48h,
        snapshot.reddit_average_comments_48h,
        snapshot.reddit_accounts_active_48h,
        snapshot.telegram_channel_user_count,
        timeout=30.0,
    )


async def _flush_community_rows_for_pairs(
    pg_conn: asyncpg.Connection,
    adapter: CoinGeckoAdapter,
    targets: list[tuple[str, str]],
) -> int:
    """Fetch and insert one row per ``(base, coingecko_slug)`` pair."""
    written_rows = 0
    for base_asset, slug in targets:
        try:
            envelope = await adapter.fetch_community_data(slug)
            await _persist_community_snapshot(pg_conn, base_asset, envelope)
            written_rows += 1
        except Exception as exc_asset:
            logger.warning(
                "community_snapshot_asset_failed | asset={} | err={}",
                base_asset,
                exc_asset,
            )
    return written_rows


async def run_daily_community_snapshot_job(
    redis_client: redis_asyncio.Redis,
    *,
    pg_pool: asyncpg.Pool,
    adapter: CoinGeckoAdapter,
    pair_builder: Callable[[], list[tuple[str, str]]] | None = None,
) -> int:
    """
    Snapshot enriched community payloads for curated universe constituents.

    Returns:
        Count of Postgres rows flushed during the run (cold-path job semantics).
    """
    factories = pair_builder or default_universe_coingecko_pairs
    locked = False
    written_rows = 0

    lock_acquired = await redis_client.set(
        COMMUNITY_SNAPSHOT_LOCK_KEY,
        "locked",
        nx=True,
        ex=COMMUNITY_SNAPSHOT_LOCK_TTL_SECONDS,
    )

    try:
        if lock_acquired is not True:
            logger.info(
                "community_snapshot_lock_held | key={}",
                COMMUNITY_SNAPSHOT_LOCK_KEY,
            )
            return written_rows

        locked = True
        targets = factories()

        async with pg_pool.acquire() as pg_conn:
            written_rows = await _flush_community_rows_for_pairs(pg_conn, adapter, targets)
        return written_rows

    except Exception as exc_job:
        logger.error("community_snapshot_job_failed | err={}", exc_job)
        return written_rows

    finally:
        if locked:
            await redis_client.delete(COMMUNITY_SNAPSHOT_LOCK_KEY)


async def community_fundamentals_loop(
    redis_client: redis_asyncio.Redis,
    *,
    pg_pool: asyncpg.Pool | None,
    settings: PolarisSettings,
    adapter: CoinGeckoAdapter,
) -> None:
    """Daily Postgres snapshots and optional universe admission refresh."""
    from atlas.universe.refresh import sync_polaris_universe_all_redis

    interval = int(settings.community_snapshot_job_interval_seconds)
    jitter = max(60, min(3600, interval // 20))
    logger.info(
        "community_fundamentals_loop_started | interval_s={} | snapshot={} | gate={}",
        interval,
        settings.community_snapshot_job_enabled,
        settings.community_admission_gate_enabled,
    )
    while True:
        try:
            if settings.community_snapshot_job_enabled and pg_pool is not None:
                await run_daily_community_snapshot_job(
                    redis_client,
                    pg_pool=pg_pool,
                    adapter=adapter,
                )
            if settings.community_admission_gate_enabled:
                await sync_polaris_universe_all_redis(
                    redis_client,
                    settings=settings,
                    adapter=adapter,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("community_fundamentals_tick_failed | err={}", exc)
        sleep_s = interval + random.uniform(-jitter, jitter)
        await asyncio.sleep(max(3600.0, sleep_s))

