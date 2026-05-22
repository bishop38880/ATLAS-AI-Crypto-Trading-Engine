"""Tests for CoinGecko dual-key sequential failover on 429."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from atlas.monitoring.key_pool import ApiKeySlot, FailoverKeyPool
from atlas.monitoring.sources.coingecko import CoinGeckoHourlySource
from atlas.shared.config import PolarisSettings


def _mock_response(status: int, body: bytes = b"{}") -> httpx.Response:
    request = httpx.Request("GET", "https://api.coingecko.com/api/v3/simple/price")
    return httpx.Response(status, request=request, content=body)


@pytest.mark.asyncio
async def test_fetch_spot_fails_over_to_secondary_on_429() -> None:
    settings = PolarisSettings()  # type: ignore[call-arg]
    pool = FailoverKeyPool(
        [
            ApiKeySlot(key_id="primary", secret="key-a"),
            ApiKeySlot(key_id="secondary", secret="key-b"),
        ]
    )
    http = AsyncMock()
    ok_body = b'{"bitcoin":{"usd":50000,"usd_24h_vol":1000,"usd_market_cap":900000}}'

    call_count = 0

    async def _get_side_effect(*_args: object, **_kwargs: object) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(429)
        return _mock_response(200, ok_body)

    http.get = AsyncMock(side_effect=_get_side_effect)
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.setex = AsyncMock()

    source = CoinGeckoHourlySource(settings, http, pool, redis)

    breaker = MagicMock()

    async def _call_success(func: object, *_args: object, **_kwargs: object) -> object:
        return await func()  # type: ignore[misc,operator]

    breaker.call = AsyncMock(side_effect=_call_success)

    with patch(
        "atlas.monitoring.sources.coingecko.get_circuit_breaker",
        return_value=breaker,
    ):
        with patch(
            "atlas.monitoring.sources.coingecko.COINGECKO_SIMPLE_PRICE_ID_BY_BASE",
            {"BTC": "bitcoin"},
        ):
            price, _vol, _cap, key_id, _lat, status = await source.fetch_spot("BTC")

    assert price == Decimal("50000")
    assert key_id == "secondary"
    assert status == "ok"
    assert call_count == 2
