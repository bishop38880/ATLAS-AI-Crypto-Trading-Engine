"""Tests for risk governor staged-exit publish endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.risk_governor import router as risk_governor_router


@pytest.mark.asyncio
async def test_staged_exit_partial_publish() -> None:
    app = FastAPI()
    mock_redis = MagicMock()
    mock_redis.publish = AsyncMock(return_value=1)
    app.state.redis = mock_redis
    app.include_router(risk_governor_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/risk-governor/staged-exit",
            json={
                "orderId": "ord-1",
                "symbol": "BTC",
                "direction": "LONG",
                "closePct": 0.5,
                "reason": "test_partial",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "partial_close_published"
    mock_redis.publish.assert_awaited_once()
