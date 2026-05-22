"""Tests for the cold-path CoinGecko community snapshot job."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

from atlas.jobs.community_snapshot_job import (
    COMMUNITY_SNAPSHOT_LOCK_KEY,
    COMMUNITY_SNAPSHOT_LOCK_TTL_SECONDS,
    run_daily_community_snapshot_job,
)
from atlas.providers.coingecko.adapter import CoinGeckoAdapter
from atlas.providers.coingecko.models import CommunityData


def _healthy_snapshot(asset_symbol: str) -> CommunityData:
    """Synthetic healthy payload for Postgres persistence mocks."""
    from datetime import datetime, timezone

    return CommunityData(
        asset_symbol=asset_symbol,
        twitter_followers=10_000,
        reddit_subscribers=60_000,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=5000,
        last_updated_utc=datetime.now(timezone.utc),
        status="healthy",
    )


class _FakeAcquire:
    """Minimal async pool acquire compatible with AsyncMock patterns."""

    def __init__(self, pg_conn: Any) -> None:
        self._pg_conn = pg_conn

    @asynccontextmanager
    async def __call__(self) -> AsyncGenerator[Any, None]:
        yield self._pg_conn


@pytest.mark.asyncio
async def test_snapshot_job_iterates_universe() -> None:
    """Each configured pair triggers exactly one enriched fetch."""

    redis_client = AsyncMock()
    redis_client.set = AsyncMock(return_value=True)
    redis_client.delete = AsyncMock(return_value=True)

    pg_execute = AsyncMock(return_value=None)
    pg_conn = AsyncMock()
    pg_conn.execute = pg_execute

    pg_pool = AsyncMock()
    pg_pool.acquire = _FakeAcquire(pg_conn)

    adapter = AsyncMock(spec=CoinGeckoAdapter)
    adapter.fetch_community_data = AsyncMock(
        side_effect=lambda slug: _healthy_snapshot(slug.upper()),
    )

    pairs = [("BTC", "bitcoin"), ("ETH", "ethereum")]

    written = await run_daily_community_snapshot_job(
        redis_client,
        pg_pool=pg_pool,
        adapter=adapter,  # type: ignore[arg-type]
        pair_builder=lambda: pairs,
    )

    assert written == len(pairs)
    assert adapter.fetch_community_data.call_count == len(pairs)
    adapter.fetch_community_data.assert_any_call("bitcoin")
    adapter.fetch_community_data.assert_any_call("ethereum")


@pytest.mark.asyncio
async def test_snapshot_job_continues_on_per_asset_failure() -> None:
    """Per-asset adapter failures warn but flush remaining rows."""

    redis_client = AsyncMock()
    redis_client.set = AsyncMock(return_value=True)
    redis_client.delete = AsyncMock(return_value=True)

    pg_execute = AsyncMock(return_value=None)
    pg_conn = AsyncMock()
    pg_conn.execute = pg_execute

    pg_pool = AsyncMock()
    pg_pool.acquire = _FakeAcquire(pg_conn)

    adapter = AsyncMock(spec=CoinGeckoAdapter)

    async def _flaky(slug: str) -> CommunityData:
        if slug == "boom":
            raise RuntimeError("edge burst")
        return _healthy_snapshot(slug.upper())

    adapter.fetch_community_data = AsyncMock(side_effect=_flaky)

    pairs = [("BAD", "boom"), ("GOOD", "litecoin")]

    written = await run_daily_community_snapshot_job(
        redis_client,
        pg_pool=pg_pool,
        adapter=adapter,  # type: ignore[arg-type]
        pair_builder=lambda: pairs,
    )

    assert written == 1
    assert pg_execute.await_count == 1


@pytest.mark.asyncio
async def test_snapshot_job_acquires_redis_lock() -> None:
    """Redis mutual exclusion primes before invoking provider I/O."""

    redis_client = AsyncMock()
    redis_client.set = AsyncMock(return_value=True)
    redis_client.delete = AsyncMock(return_value=True)

    pg_execute = AsyncMock(return_value=None)
    pg_conn = AsyncMock()
    pg_conn.execute = pg_execute

    pg_pool = AsyncMock()
    pg_pool.acquire = _FakeAcquire(pg_conn)

    adapter = AsyncMock(spec=CoinGeckoAdapter)
    adapter.fetch_community_data = AsyncMock(side_effect=lambda s: _healthy_snapshot(s.upper()))

    event_order: list[str] = []

    async def traced_set(*args: object, **kwargs: object) -> bool:
        event_order.append("lock")
        return True

    redis_client.set = AsyncMock(side_effect=traced_set)

    original_fetch: Callable[..., Any] = adapter.fetch_community_data

    async def traced_fetch(slug: str) -> CommunityData:
        event_order.append("fetch")
        return await original_fetch(slug)

    adapter.fetch_community_data = AsyncMock(side_effect=traced_fetch)

    pairs = [("BTC", "bitcoin")]
    await run_daily_community_snapshot_job(
        redis_client,
        pg_pool=pg_pool,
        adapter=adapter,  # type: ignore[arg-type]
        pair_builder=lambda: pairs,
    )

    assert event_order[0] == "lock"
    assert "fetch" in event_order

    redis_client.set.assert_called_once()
    kw = redis_client.set.call_args.kwargs
    assert kw.get("nx") is True
    assert kw.get("ex") == COMMUNITY_SNAPSHOT_LOCK_TTL_SECONDS
    positional = redis_client.set.call_args[0]
    assert positional[0] == COMMUNITY_SNAPSHOT_LOCK_KEY


@pytest.mark.asyncio
async def test_snapshot_job_skips_if_lock_held() -> None:
    """If another replica owns the TTL lock, skips work silently."""

    redis_client = AsyncMock()
    redis_client.set = AsyncMock(return_value=False)
    redis_client.delete = AsyncMock(return_value=True)

    pg_execute = AsyncMock(return_value=None)
    pg_conn = AsyncMock()
    pg_conn.execute = pg_execute

    pg_pool = AsyncMock()
    pg_pool.acquire = _FakeAcquire(pg_conn)

    adapter = AsyncMock(spec=CoinGeckoAdapter)

    pairs = [("BTC", "bitcoin")]
    rows = await run_daily_community_snapshot_job(
        redis_client,
        pg_pool=pg_pool,
        adapter=adapter,  # type: ignore[arg-type]
        pair_builder=lambda: pairs,
    )

    assert rows == 0
    adapter.fetch_community_data.assert_not_called()
    pg_execute.assert_not_awaited()
    redis_client.delete.assert_not_called()
