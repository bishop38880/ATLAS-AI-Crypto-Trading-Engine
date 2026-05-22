"""Smoke tests for Redis provider bundle assembly for OmniBox."""

from __future__ import annotations

import msgspec
import pytest

from atlas.api.omnibox_provider_context import build_omnibox_redis_provider_context
from atlas.core.provider_health import ProviderHealthState
from atlas.shared.config import PolarisSettings


@pytest.fixture
async def fake_redis():
    import fakeredis.aioredis

    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    yield client
    await client.flushall()


@pytest.mark.asyncio
async def test_provider_bundle_includes_price_and_fred(fake_redis) -> None:
    settings = PolarisSettings()
    await fake_redis.set(
        "atlas:price:BTC",
        msgspec.json.encode({"price": "50000", "conf": "1", "publish_time": 1_700_000_000}),
    )
    await fake_redis.set(
        "atlas:provider:fred:macro_snapshot",
        msgspec.json.encode(
            {
                "fed_funds_rate": "2.5",
                "yield_curve": {"ten_year": "4.1", "two_year": "3.8", "spread": "0.3"},
                "cpi_yoy": "3.1",
                "stablecoin_total_supply_usd": "120000000000",
                "macro_regime": "NEUTRAL",
                "stale": False,
                "status": "healthy",
            },
        ),
    )
    text = await build_omnibox_redis_provider_context(fake_redis, settings, "BTC")
    assert text is not None
    assert "50000" in text
    assert "FRED macro" in text
    assert "RISK_ON" in text or "NEUTRAL" in text


@pytest.mark.asyncio
async def test_provider_bundle_includes_degraded_health(fake_redis) -> None:
    settings = PolarisSettings()
    await fake_redis.set(
        "atlas:price:BTC",
        msgspec.json.encode({"price": "1", "conf": "1", "publish_time": 1}),
    )
    bad = ProviderHealthState(health_score=0.5, ema_latency=0.2, adaptive_timeout=5.0)
    await fake_redis.set("provider:coinalyze:health_score", msgspec.json.encode(bad))
    text = await build_omnibox_redis_provider_context(fake_redis, settings, "BTC")
    assert text is not None
    assert "Coinalyze=0.50" in text or "Coinalyze=0.5" in text
    assert "Provider health" in text
