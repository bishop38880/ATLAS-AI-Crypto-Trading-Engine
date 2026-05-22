"""Tests for last-known-good failover routing."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.monitoring.last_known_good import write_last_known_good
from atlas.monitoring.models import NormalizedHourlyQuote
from atlas.monitoring.router import HourlyProviderRouter
from atlas.shared.config import PolarisSettings


@pytest.mark.asyncio
async def test_router_uses_last_known_good_when_primary_fails() -> None:
    settings = PolarisSettings()  # type: ignore[call-arg]
    redis = AsyncMock()
    cached = NormalizedHourlyQuote(
        asset_base="BTC",
        sampled_at=datetime(2026, 5, 18, tzinfo=timezone.utc),
        price_usd=Decimal("50000"),
        source_provider="coingecko",
        source_key_id="primary",
        quality="full",
    )
    redis.get = AsyncMock(return_value=None)

    coingecko = MagicMock()
    coingecko.fetch_spot = AsyncMock(side_effect=RuntimeError("upstream down"))
    pyth = MagicMock()
    pyth.fetch_spot = AsyncMock(return_value=(None, 1.0, "degraded"))

    async def _get_side_effect(key: str) -> bytes | None:
        if "lkg" in key:
            import msgspec

            return msgspec.json.encode(cached.model_dump(mode="json"))
        return None

    redis.get = AsyncMock(side_effect=_get_side_effect)

    router = HourlyProviderRouter(settings, coingecko, pyth, redis)
    quote = await router.fetch_normalized_quote("BTC")
    assert quote.price_usd == Decimal("50000")
    assert quote.quality == "degraded"
    assert "last_known_good" in quote.degraded_reasons


@pytest.mark.asyncio
async def test_write_and_read_last_known_good() -> None:
    redis = AsyncMock()
    stored: dict[str, bytes] = {}

    async def _setex(key: str, _ttl: int, value: bytes) -> None:
        stored[key] = value

    redis.setex = AsyncMock(side_effect=_setex)
    redis.get = AsyncMock(side_effect=lambda key: stored.get(key))

    quote = NormalizedHourlyQuote(
        asset_base="ETH",
        sampled_at=datetime.now(timezone.utc),
        price_usd=Decimal("3000"),
        source_provider="coingecko",
        source_key_id="primary",
    )
    await write_last_known_good(redis, quote, 60)
    from atlas.monitoring.last_known_good import read_last_known_good

    loaded = await read_last_known_good(redis, "ETH")
    assert loaded is not None
    assert loaded.price_usd == Decimal("3000")
