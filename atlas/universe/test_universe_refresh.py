"""Tests for community-gated universe Redis sync."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest


class _FakeRedisPipeline:
    """Minimal pipeline stub for universe sync tests."""

    def __init__(self) -> None:
        self.deleted: list[str] = []
        self.added: list[tuple[str, ...]] = []

    def delete(self, key: str) -> _FakeRedisPipeline:
        self.deleted.append(key)
        return self

    def sadd(self, key: str, *members: str) -> _FakeRedisPipeline:
        self.added.append((key, *members))
        return self

    async def execute(self) -> None:
        return None


class _FakeRedis:
    """Redis client stub returning a synchronous pipeline."""

    def __init__(self) -> None:
        self.last_pipe: _FakeRedisPipeline | None = None

    def pipeline(self) -> _FakeRedisPipeline:
        self.last_pipe = _FakeRedisPipeline()
        return self.last_pipe

from atlas.providers.coingecko.models import CommunityData
from atlas.shared.config import PolarisSettings
from atlas.universe.refresh import (
    POLARIS_UNIVERSE_ALL_KEY,
    collect_admitted_universe_members,
    sync_polaris_universe_all_redis,
)


def _community(
    *,
    reddit: int | None,
    telegram: int | None,
    status: str = "healthy",
) -> CommunityData:
    return CommunityData(
        asset_symbol="TEST",
        twitter_followers=None,
        reddit_subscribers=reddit,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=telegram,
        last_updated_utc=datetime.now(timezone.utc),
        status=status,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_collect_admitted_universe_filters_ghost(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.core.asset_universe import AssetConfig, AssetTier

    tiny_universe = [
        AssetConfig(symbol="BTC/USDT", tier=AssetTier.ALWAYS_ON, group="btc"),
        AssetConfig(symbol="ETH/USDT", tier=AssetTier.ALWAYS_ON, group="eth_l1"),
    ]
    monkeypatch.setattr("atlas.universe.refresh.ASSET_UNIVERSE", tiny_universe)

    async def fake_fetch(slug: str) -> CommunityData:
        if slug == "bitcoin":
            return _community(reddit=50_000, telegram=10_000)
        return _community(reddit=10, telegram=5)

    adapter = MagicMock()
    adapter.fetch_community_data = AsyncMock(side_effect=fake_fetch)
    members = await collect_admitted_universe_members(adapter)
    assert members == ("BTCUSDT",)


@pytest.mark.asyncio
async def test_sync_without_gate_writes_full_universe() -> None:
    redis = _FakeRedis()

    settings = PolarisSettings.model_construct(community_admission_gate_enabled=False)
    count = await sync_polaris_universe_all_redis(redis, settings=settings, adapter=None)

    assert count > 100
    pipe = redis.last_pipe
    assert pipe is not None
    assert pipe.deleted == [POLARIS_UNIVERSE_ALL_KEY]
    assert pipe.added[0][0] == POLARIS_UNIVERSE_ALL_KEY


@pytest.mark.asyncio
async def test_sync_with_gate_uses_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    redis = _FakeRedis()

    collect_mock = AsyncMock(return_value=("BTCUSDT",))
    monkeypatch.setattr(
        "atlas.universe.refresh.collect_admitted_universe_members",
        collect_mock,
    )
    adapter = MagicMock()
    settings = PolarisSettings.model_construct(community_admission_gate_enabled=True)
    count = await sync_polaris_universe_all_redis(redis, settings=settings, adapter=adapter)

    collect_mock.assert_awaited_once_with(adapter)
    assert count == 1
    pipe = redis.last_pipe
    assert pipe is not None
    assert pipe.added == [(POLARIS_UNIVERSE_ALL_KEY, "BTCUSDT")]
