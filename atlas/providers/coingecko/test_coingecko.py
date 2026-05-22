"""Tests for CoinGecko / GeckoTerminal Provider Adapter.

Covers all 7 mandated test cases:
    1. test_fetch_price_reference_returns_valid_model
    2. test_fetch_price_reference_cache_hit_skips_api
    3. test_fetch_price_reference_marks_degraded_on_failure
    4. test_fetch_dex_pool_data_returns_list
    5. test_fetch_dex_pool_data_marks_degraded_on_failure
    6. test_price_divergence_threshold_constant_is_0005
    7. test_adapter_registers_and_returns_healthy
"""

import msgspec
from decimal import Decimal
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
import redis.asyncio as redis_async

from atlas.providers.coingecko.adapter import (
    COINGECKO_PRICE_TTL,
    CoinGeckoAdapter,
    DexPoolData,
    GECKOTERM_POOL_TTL,
    PRICE_DIVERGENCE_THRESHOLD,
    PriceReferenceData,
)


# ── Fixtures ─────────────────────────────────────────────────────
@pytest.fixture
def mock_redis() -> AsyncMock:
    """Return a mock Redis client with empty cache by default."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.setex = AsyncMock(return_value=True)
    return client


@pytest_asyncio.fixture
async def adapter(mock_redis: AsyncMock) -> AsyncGenerator[CoinGeckoAdapter, None]:
    """Return a CoinGeckoAdapter wired to mock Redis."""
    adp = CoinGeckoAdapter(redis_client=mock_redis)
    yield adp
    await adp.close()


# ── 1. Price reference returns valid model ───────────────────────
@pytest.mark.asyncio
async def test_fetch_price_reference_returns_valid_model(
    adapter: CoinGeckoAdapter,
) -> None:
    """Mock httpx. Assert PriceReferenceData fields populated correctly."""
    asset = "bitcoin"
    mock_response = httpx.Response(
        200,
        json={
            asset: {
                "usd": 67432.12,
                "usd_market_cap": 1_320_000_000_000,
                "usd_24h_vol": 28_500_000_000,
                "last_updated_at": 1_704_000_000,
            },
        },
        request=httpx.Request("GET", f"https://api.coingecko.com/api/v3/simple/price"),
    )

    adapter._cg_client = AsyncMock()
    adapter._cg_client.get.return_value = mock_response

    result = await adapter.fetch_price_reference(asset)

    assert isinstance(result, PriceReferenceData)
    assert result.asset == asset
    assert result.price_usd == "67432.12"
    assert result.market_cap_usd == 1_320_000_000_000
    assert result.volume_24h == 28_500_000_000
    assert result.status == "healthy"
    assert "2023-12-31" in result.last_updated_utc


# ── 2. Cache hit skips API ───────────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_price_reference_cache_hit_skips_api(
    adapter: CoinGeckoAdapter,
    mock_redis: AsyncMock,
) -> None:
    """Seed Redis mock with cached value. Assert httpx never called."""
    asset = "ethereum"
    cached_model = PriceReferenceData(
        asset=asset,
        price_usd="3450.55",
        market_cap_usd=Decimal("415000000000"),
        volume_24h=Decimal("12000000000"),
        last_updated_utc="2026-04-24T12:00:00+00:00",
        status="healthy",
    )
    mock_redis.get.return_value = cached_model.model_dump_json()

    adapter._cg_client = AsyncMock()

    result = await adapter.fetch_price_reference(asset)

    assert result.asset == asset
    assert result.price_usd == "3450.55"
    adapter._cg_client.get.assert_not_called()


# ── 3. Marks degraded on failure ─────────────────────────────────
@pytest.mark.asyncio
async def test_fetch_price_reference_marks_degraded_on_failure(
    adapter: CoinGeckoAdapter,
) -> None:
    """Mock httpx to raise. Assert status='degraded' returned, no raise."""
    asset = "bitcoin"
    adapter._cg_client = AsyncMock()
    adapter._cg_client.get.side_effect = httpx.RequestError("API Down")

    result = await adapter.fetch_price_reference(asset)

    assert result.status == "degraded"
    assert result.price_usd == "0"
    assert adapter.status == "DEGRADED"


def _mock_dex_pool_response() -> httpx.Response:
    """Build a mock GeckoTerminal DEX pool API response."""
    return httpx.Response(
        200,
        json={"data": [
            {"attributes": {
                "address": "0xpool1", "dex_id": "uniswap_v3",
                "base_token_price_usd": "3450.12", "reserve_in_usd": "25000000",
                "volume_usd": {"h24": "8000000"},
                "price_change_percentage": {"h24": "-2.5"},
            }},
            {"attributes": {
                "address": "0xpool2", "dex_id": "sushiswap",
                "base_token_price_usd": "3448.88", "reserve_in_usd": "12000000",
                "volume_usd": {"h24": "3000000"},
                "price_change_percentage": {"h24": "1.2"},
            }},
        ]},
        request=httpx.Request("GET", "https://api.geckoterminal.com/api/v2/networks"),
    )


@pytest.mark.asyncio
async def test_fetch_dex_pool_data_returns_list(
    adapter: CoinGeckoAdapter,
) -> None:
    """Mock httpx. Assert list[DexPoolData] with correct fields."""
    asset = "eth/0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    adapter._gt_client = AsyncMock()
    adapter._gt_client.get.return_value = _mock_dex_pool_response()
    result = await adapter.fetch_dex_pool_data(asset)
    assert isinstance(result, list)
    assert len(result) == 2
    assert all(isinstance(p, DexPoolData) for p in result)
    pool = result[0]
    assert pool.pool_address == "0xpool1"
    assert pool.dex_name == "uniswap_v3"
    assert pool.network == "eth"
    assert pool.price_usd == "3450.12"
    assert pool.liquidity_usd == 25_000_000.0
    assert pool.volume_24h == 8_000_000.0
    assert pool.price_change_24h_pct == -2.5
    assert pool.status == "healthy"


# ── 5. DEX pool marks degraded on failure ────────────────────────
@pytest.mark.asyncio
async def test_fetch_dex_pool_data_marks_degraded_on_failure(
    adapter: CoinGeckoAdapter,
) -> None:
    """Mock httpx to raise. Assert empty list returned, no raise."""
    asset = "eth/0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    adapter._gt_client = AsyncMock()
    adapter._gt_client.get.side_effect = httpx.RequestError("Network Error")

    result = await adapter.fetch_dex_pool_data(asset)

    assert result == []
    assert adapter.status == "DEGRADED"


# ── 6. Price divergence threshold constant ───────────────────────
def test_price_divergence_threshold_constant_is_0005() -> None:
    """Assert PRICE_DIVERGENCE_THRESHOLD == 0.005."""
    assert PRICE_DIVERGENCE_THRESHOLD == 0.005


# ── 7. Smoke: adapter registers and returns healthy ──────────────
@pytest.mark.asyncio
async def test_adapter_registers_and_returns_healthy(
    adapter: CoinGeckoAdapter,
) -> None:
    """Instantiate CoinGeckoAdapter. Call get_health_status() with mocked ping."""
    mock_response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.coingecko.com/api/v3/ping"),
    )
    adapter._cg_client = AsyncMock()
    adapter._cg_client.get.return_value = mock_response

    health = await adapter.get_health_status()

    assert health.name == "coingecko"
    assert health.status == "HEALTHY"
    assert health.last_update > 0
