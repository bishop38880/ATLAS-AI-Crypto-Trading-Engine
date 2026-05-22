"""Integration tests for Helius webhook and signals REST routes."""

from __future__ import annotations

import time

import fakeredis.aioredis
import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.helius_providers import router as helius_providers_router
from atlas.api.routes.helius_webhooks import router as helius_webhooks_router
from atlas.services import solana_flow_tracker

_BINANCE = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"


@pytest.fixture
async def helius_app():
    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    solana_flow_tracker.init_redis(redis_client)

    app = FastAPI()
    app.state.redis = redis_client

    async def _mock_prices(_client: httpx.AsyncClient) -> tuple[float, float]:
        return 150.0, 1.25

    import atlas.api.routes.helius_webhooks as webhook_module

    original_fetch = webhook_module.fetch_sol_jup_prices_usd
    webhook_module.fetch_sol_jup_prices_usd = _mock_prices  # type: ignore[assignment]

    mock_http = httpx.AsyncClient()
    app.state.dashboard_market_http = mock_http

    app.include_router(helius_webhooks_router)
    app.include_router(helius_providers_router)

    yield app

    webhook_module.fetch_sol_jup_prices_usd = original_fetch
    await mock_http.aclose()
    solana_flow_tracker.init_redis(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_webhook_accepts_and_updates_signals(helius_app: FastAPI) -> None:
    payload = [
        {
            "signature": "test_sig_route_1",
            "timestamp": time.time(),
            "nativeTransfers": [
                {
                    "fromUserAccount": "whale_wallet_abc",
                    "toUserAccount": _BINANCE,
                    "amount": 500_000_000_000,
                },
            ],
        },
    ]
    transport = ASGITransport(app=helius_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        post = await client.post("/api/webhooks/helius", json=payload)
        assert post.status_code == 200
        assert post.json()["count"] == 1

        signals = await client.get("/api/providers/helius/signals/SOL")
        assert signals.status_code == 200
        body = signals.json()
        assert body["status"] == "active"
        assert body["signals"]["whaleTxCount24H"] >= 1


@pytest.mark.asyncio
async def test_signals_rejects_non_solana_symbol(helius_app: FastAPI) -> None:
    transport = ASGITransport(app=helius_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/providers/helius/signals/BTC")
        assert resp.status_code == 400
