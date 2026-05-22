"""``read_prices`` should prefer Pyth / CoinGecko cache over stale Bitget rows."""

from __future__ import annotations

import msgspec
import pytest

from atlas.api._channel_reads import read_prices
from atlas.shared.config import PolarisSettings


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()


@pytest.mark.asyncio
async def test_read_prices_prefers_pyth_over_bitget(fake_redis) -> None:
    settings = PolarisSettings()
    await fake_redis.set(
        "provider:bitget:price:BTC",
        msgspec.json.encode(
            {
                "symbol": "BTC",
                "price": "28000",
                "change_24h": 0.0,
                "volume_24h": "0",
                "timestamp": "2023-01-01T00:00:00Z",
            },
        ),
    )
    await fake_redis.set(
        "atlas:price:BTC",
        msgspec.json.encode(
            {
                "price": "97500.12",
                "conf": "1.5",
                "publish_time": 1_700_000_000,
            },
        ),
    )
    rows = await read_prices(fake_redis, settings, symbols_query="BTC")
    assert len(rows) == 1
    assert rows[0].price == "97500.12"
    assert rows[0].symbol == "BTC"


@pytest.mark.asyncio
async def test_read_prices_falls_back_to_bitget_when_no_oracle(fake_redis) -> None:
    settings = PolarisSettings()
    await fake_redis.set(
        "provider:bitget:price:BTC",
        msgspec.json.encode(
            {
                "symbol": "BTC",
                "price": "65000",
                "change_24h": 1.5,
                "volume_24h": "100",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ),
    )
    rows = await read_prices(fake_redis, settings, symbols_query="BTC")
    assert len(rows) == 1
    assert rows[0].price == "65000"
