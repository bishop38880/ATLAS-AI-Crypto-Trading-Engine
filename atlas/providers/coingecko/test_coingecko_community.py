"""Focused tests for CoinGecko fundamentals community enrichment surface."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import msgspec
import pytest

from atlas.providers.coingecko.adapter import COMMUNITY_DATA_TTL, CoinGeckoAdapter


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.setex = AsyncMock(return_value=True)
    return redis_client


@pytest.fixture
async def community_adapter(mock_redis: AsyncMock) -> CoinGeckoAdapter:
    adapter = CoinGeckoAdapter(redis_client=mock_redis)
    try:
        yield adapter
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_fetch_community_data_returns_valid_model(
    community_adapter: CoinGeckoAdapter,
) -> None:
    """Mock CoinGecko. Assert Community fields mirror ``community_data`` payload."""
    from atlas.providers.coingecko.models import CommunityData

    coin_id = "bitcoin"
    body = {
        "id": coin_id,
        "symbol": "btc",
        "community_data": {
            "twitter_followers": 10_004_921,
            "reddit_subscribers": 3_521_884,
            "reddit_average_posts_48h": 120.42,
            "reddit_average_comments_48h": 990.81,
            "reddit_accounts_active_48h": 48_832.25,
            "telegram_channel_user_count": 251_993,
        },
    }
    response = httpx.Response(
        200,
        content=msgspec.json.encode(body),
        request=httpx.Request("GET", "https://api.coingecko.com/api/v3/coins/bitcoin"),
    )
    community_adapter._cg_client = AsyncMock()
    community_adapter._cg_client.get.return_value = response

    model = await community_adapter.fetch_community_data(coin_id)

    assert isinstance(model, CommunityData)
    assert model.asset_symbol == "BTC"
    assert model.twitter_followers == 10004921
    assert model.reddit_subscribers == 3521884
    assert pytest.approx(model.reddit_average_posts_48h or 0.0, rel=1e-6) == 120.42
    assert pytest.approx(model.reddit_average_comments_48h or 0.0, rel=1e-6) == 990.81
    assert pytest.approx(model.reddit_accounts_active_48h or 0.0, rel=1e-6) == 48832.25
    assert model.telegram_channel_user_count == 251993
    assert model.status == "healthy"


@pytest.mark.asyncio
async def test_fetch_community_data_cache_hit_skips_api(
    community_adapter: CoinGeckoAdapter,
    mock_redis: AsyncMock,
) -> None:
    """Seed Redis. Assert outbound HTTP suppressed on cache positives."""
    from atlas.providers.coingecko.models import CommunityData

    coin_id = "ethereum"
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    cached_payload = CommunityData(
        asset_symbol="ETH",
        twitter_followers=2_040_991,
        reddit_subscribers=1_294_883,
        reddit_average_posts_48h=None,
        reddit_average_comments_48h=None,
        reddit_accounts_active_48h=None,
        telegram_channel_user_count=12_993,
        last_updated_utc=ts,
        status="healthy",
    )
    mock_redis.get.return_value = msgspec.json.encode(cached_payload.model_dump(mode="json"))
    community_adapter._cg_client = AsyncMock()

    result = await community_adapter.fetch_community_data(coin_id)

    community_adapter._cg_client.get.assert_not_called()
    assert result.asset_symbol == "ETH"
    assert result.status == "healthy"


@pytest.mark.asyncio
async def test_fetch_community_data_marks_degraded_on_failure(
    community_adapter: CoinGeckoAdapter,
    mock_redis: AsyncMock,
) -> None:
    """Upstream outage produces empty fundamentals rows without bubbling."""
    from atlas.providers.coingecko.models import CommunityData

    coin_id = "solana"
    community_adapter._cg_client = AsyncMock()
    community_adapter._cg_client.get.side_effect = httpx.TimeoutException("edge timeout")

    payload = await community_adapter.fetch_community_data(coin_id)

    assert isinstance(payload, CommunityData)
    assert payload.status == "degraded"
    assert payload.twitter_followers is None
    assert payload.telegram_channel_user_count is None
    assert community_adapter.status == "DEGRADED"
    mock_redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_community_data_handles_null_telegram(
    community_adapter: CoinGeckoAdapter,
) -> None:
    """Telegram user counts absent from JSON still validate."""
    coin_id = "bitcoin"
    body = {
        "id": coin_id,
        "symbol": "btc",
        "community_data": {
            "twitter_followers": 100,
            "reddit_subscribers": 200,
            "reddit_average_posts_48h": 1.0,
            "reddit_average_comments_48h": 2.0,
            "reddit_accounts_active_48h": 3.0,
            "telegram_channel_user_count": None,
        },
    }
    response = httpx.Response(
        200,
        content=msgspec.json.encode(body),
        request=httpx.Request("GET", "https://example.invalid/coins/test"),
    )
    community_adapter._cg_client = AsyncMock()
    community_adapter._cg_client.get.return_value = response

    model = await community_adapter.fetch_community_data(coin_id)

    assert model.telegram_channel_user_count is None


@pytest.mark.asyncio
async def test_fetch_community_data_handles_null_reddit_averages(
    community_adapter: CoinGeckoAdapter,
) -> None:
    """48h Reddit velocity fields tolerate nulls."""
    coin_id = "bitcoin"
    body = {
        "id": coin_id,
        "symbol": "btc",
        "community_data": {
            "twitter_followers": None,
            "reddit_subscribers": None,
            "reddit_average_posts_48h": None,
            "reddit_average_comments_48h": None,
            "reddit_accounts_active_48h": None,
            "telegram_channel_user_count": 50,
        },
    }
    response = httpx.Response(
        200,
        content=msgspec.json.encode(body),
        request=httpx.Request("GET", "https://example.invalid/coins/null48"),
    )
    community_adapter._cg_client = AsyncMock()
    community_adapter._cg_client.get.return_value = response

    model = await community_adapter.fetch_community_data(coin_id)

    assert model.reddit_average_posts_48h is None
    assert model.reddit_average_comments_48h is None
    assert model.reddit_accounts_active_48h is None


def test_community_data_ttl_is_21600() -> None:
    """Document fundamentals cadence (non hot-path caching)."""
    assert COMMUNITY_DATA_TTL == 21600

