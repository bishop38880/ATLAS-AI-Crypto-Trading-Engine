"""Tests for monitoring startup synthesis (agents / prices connectivity)."""

from __future__ import annotations

import msgspec
import pytest

from atlas.api.routes.monitoring import get_metrics
from atlas.api.monitoring_startup_checks import startup_agents_step_ok, startup_prices_step_ok


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()


@pytest.mark.asyncio
async def test_agents_step_passes_when_status_keys_exist(fake_redis) -> None:
    await fake_redis.set(
        "agent:TechnicalAgent:status",
        msgspec.json.encode({"status": "READY", "lastPingMs": 5}),
    )
    assert await startup_agents_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_agents_step_passes_from_latest_signal_telemetry(fake_redis) -> None:
    payload = {"telemetry": {"agent_count": 1}}
    await fake_redis.set("polaris:latest_signal", msgspec.json.encode(payload))
    assert await startup_agents_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_agents_step_fails_when_no_signal_and_no_keys(fake_redis) -> None:
    assert await startup_agents_step_ok(fake_redis) is False


@pytest.mark.asyncio
async def test_prices_step_passes_for_pyth_cache_key(fake_redis) -> None:
    await fake_redis.set("atlas:price:BTC", b"{}")
    assert await startup_prices_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_prices_step_passes_for_bitget_cache_key(fake_redis) -> None:
    await fake_redis.set("provider:bitget:price:BTC", b"{}")
    assert await startup_prices_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_prices_step_passes_for_coingecko_gecko_id_cache_key(fake_redis) -> None:
    await fake_redis.set("provider:coingecko:price:bitcoin", b"{}")
    assert await startup_prices_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_prices_step_passes_when_only_non_btc_pyth_key_present(fake_redis) -> None:
    await fake_redis.set("atlas:price:SOL", b"{}")
    assert await startup_prices_step_ok(fake_redis) is True


@pytest.mark.asyncio
async def test_prices_step_fails_when_no_price_keys(fake_redis) -> None:
    assert await startup_prices_step_ok(fake_redis) is False


@pytest.mark.asyncio
async def test_metrics_includes_cost_breakdown_and_average_score(fake_redis) -> None:
    from types import SimpleNamespace

    await fake_redis.set("polaris:api_cost_today", "1.25")
    await fake_redis.set("polaris:api_cost_cap", "5.00")
    await fake_redis.set(
        "polaris:api_cost_breakdown",
        msgspec.json.encode(
            {
                "deepseek-chat": {
                    "provider": "deepseek-chat",
                    "costUsd": "1.250000",
                    "calls": 2,
                }
            }
        ),
    )
    await fake_redis.lpush(
        "polaris:latency:history",
        msgspec.json.encode({"score": 60.0, "latencyMs": 10.0}),
    )
    await fake_redis.lpush(
        "polaris:latency:history",
        msgspec.json.encode({"score": 80.0, "latencyMs": 12.0}),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=fake_redis)))

    payload = await get_metrics(request)

    assert payload["costPercent"] == "25"
    assert payload["avgScore"] == 70.0
    assert payload["costBreakdown"] == [
        {"provider": "deepseek-chat", "costUsd": "1.250000", "calls": 2}
    ]
