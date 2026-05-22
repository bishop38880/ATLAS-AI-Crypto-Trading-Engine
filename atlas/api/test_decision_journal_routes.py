"""Tests for GET /api/decisions/journal."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.decisions import router as decisions_router


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    _app.state.db_pool = None
    _app.include_router(decisions_router)
    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_decision_journal_returns_503_without_db(client: AsyncClient) -> None:
    response = await client.get("/api/decisions/journal")
    assert response.status_code == 503
    assert response.json()["detail"] == "historical_store_unavailable"


@pytest.mark.anyio
async def test_decision_journal_ok_with_mock_pool(app: FastAPI, client: AsyncClient) -> None:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=0)
    conn.fetch = AsyncMock(return_value=[])

    pool = MagicMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    app.state.db_pool = pool

    response = await client.get("/api/decisions/journal")
    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["total"] == 0
