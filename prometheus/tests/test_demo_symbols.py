import httpx
import msgspec
import pytest
import respx
from datetime import datetime, timezone
from fakeredis import FakeAsyncRedis

from prometheus.services.demo_symbols import (
    demo_to_base,
    base_to_demo,
    fetch_demo_symbols,
    REDIS_CACHE_KEY,
)
from prometheus.services.paper_trade_models import DemoSymbol
from prometheus.settings import prometheus_settings


@pytest.fixture
def redis():
    return FakeAsyncRedis()


@pytest.mark.asyncio
async def test_demo_to_base_extracts_canonical_symbol():
    assert demo_to_base("SBTCSUSDT") == "BTCUSDT"
    assert demo_to_base("SETHSUSDT") == "ETHUSDT"
    assert demo_to_base("BTCUSDT") is None        # missing leading S
    assert demo_to_base("SPEPEUSDT") is None      # not SUSDT family


def test_base_to_demo_finds_match():
    now = datetime.now(timezone.utc)
    symbols = [
        DemoSymbol(
            symbol="SBTCSUSDT",
            base_symbol="BTCUSDT",
            margin_coin="SUSDT",
            contract_type="SUSDT-FUTURES",
            fetched_at=now,
        ),
        DemoSymbol(
            symbol="SETHSUSDT",
            base_symbol="ETHUSDT",
            margin_coin="SUSDT",
            contract_type="SUSDT-FUTURES",
            fetched_at=now,
        ),
    ]
    match = base_to_demo("BTCUSDT", symbols)
    assert match is not None
    assert match.symbol == "SBTCSUSDT"
    assert base_to_demo("DOGEUSDT", symbols) is None


@pytest.mark.asyncio
async def test_fetch_demo_symbols_uses_cache(redis):
    now = datetime.now(timezone.utc)
    cached_symbols = [
        DemoSymbol(
            symbol="SBTCSUSDT",
            base_symbol="BTCUSDT",
            margin_coin="SUSDT",
            contract_type="SUSDT-FUTURES",
            fetched_at=now,
        )
    ]
    await redis.set(REDIS_CACHE_KEY, msgspec.json.encode([s.model_dump() for s in cached_symbols]))

    async with httpx.AsyncClient() as client:
        # No respx mock needed because it should hit cache first
        result = await fetch_demo_symbols(redis, client)
        assert len(result) == 1
        assert result[0].symbol == "SBTCSUSDT"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_demo_symbols_fetches_when_cache_miss(redis):
    bitget_url = f"{prometheus_settings.bitget_base_url}/api/v2/mix/market/contracts"
    fixture = {
        "code": "00000",
        "msg": "success",
        "data": [
            {
                "symbol": "SBTCSUSDT",
                "quoteCoin": "SUSDT",
            }
        ]
    }
    respx.get(bitget_url).mock(return_value=httpx.Response(200, json=fixture))

    async with httpx.AsyncClient() as client:
        result = await fetch_demo_symbols(redis, client)
        assert len(result) == 1
        assert result[0].symbol == "SBTCSUSDT"
        
        # Verify cache populated
        cached_raw = await redis.get(REDIS_CACHE_KEY)
        assert cached_raw is not None
        cached_data = msgspec.json.decode(cached_raw, type=list[dict])
        cached = [DemoSymbol(**d) for d in cached_data]
        assert cached[0].symbol == "SBTCSUSDT"


@pytest.mark.asyncio
@respx.mock
async def test_fetch_demo_symbols_falls_back_on_api_error(redis):
    bitget_url = f"{prometheus_settings.bitget_base_url}/api/v2/mix/market/contracts"
    respx.get(bitget_url).mock(side_effect=httpx.ConnectError("boom"))

    async with httpx.AsyncClient() as client:
        result = await fetch_demo_symbols(redis, client)
        # Should return fallback symbols (at least SBTCSUSDT)
        assert len(result) > 0
        assert any(s.symbol == "SBTCSUSDT" for s in result)
        
        # Fallback should NOT be cached
        assert await redis.get(REDIS_CACHE_KEY) is None


@pytest.mark.asyncio
@respx.mock
async def test_fetch_demo_symbols_skips_non_conforming_names(redis):
    bitget_url = f"{prometheus_settings.bitget_base_url}/api/v2/mix/market/contracts"
    fixture = {
        "code": "00000",
        "data": [
            {"symbol": "SBTCSUSDT"},  # Valid
            {"symbol": "INVALID"},    # Invalid
        ]
    }
    respx.get(bitget_url).mock(return_value=httpx.Response(200, json=fixture))

    async with httpx.AsyncClient() as client:
        result = await fetch_demo_symbols(redis, client)
        assert len(result) == 1
        assert result[0].symbol == "SBTCSUSDT"
