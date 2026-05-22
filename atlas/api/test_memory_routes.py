"""Smoke tests for ``/api/memory`` routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes import memory as memory_routes


@pytest.fixture()
def memory_app() -> FastAPI:
    """Minimal app with memory router and mocked state."""
    app = FastAPI()
    app.include_router(memory_routes.router)

    redis = AsyncMock()
    redis.lrange = AsyncMock(return_value=[])

    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=0)
    pool.fetch = AsyncMock(return_value=[])

    qdrant = AsyncMock()
    qdrant.get_collection = AsyncMock(
        side_effect=RuntimeError("no qdrant in test"),
    )

    app.state.redis = redis
    app.state.db_pool = pool
    app.state.qdrant_client = None
    app.state.embedding_service = MagicMock()
    app.state.embedding_service.vector_dimension = 1024

    writer = AsyncMock()
    writer.write_pattern = AsyncMock(return_value="00000000-0000-0000-0000-000000000001")
    writer.tag_signal_metadata = AsyncMock(return_value=True)
    app.state.rag_writer = writer
    return app


@pytest.mark.asyncio()
async def test_stats_returns_pg_counts(memory_app: FastAPI) -> None:
    """Stats endpoint uses Postgres when pool is present."""
    pool = memory_app.state.db_pool
    pool.fetchval = AsyncMock(side_effect=[42, 10, 3])

    transport = ASGITransport(app=memory_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/memory/stats")
    assert response.status_code == 200
    body = response.json()
    assert body["totalDocuments"] == 42
    assert body["archivedCount"] == 10
    assert body["patternMemoryRows"] == 3


@pytest.mark.asyncio()
async def test_agent_zero_returns_threshold(memory_app: FastAPI) -> None:
    """Agent Zero endpoint exposes configured threshold and schedule fields."""
    transport = ASGITransport(app=memory_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/memory/agent-zero")
    assert response.status_code == 200
    body = response.json()
    assert body["threshold"] == 0.75
    assert "nextRunRelative" in body
    assert "collectionHealthLabel" in body


@pytest.mark.asyncio()
async def test_retrievals_empty_when_redis_empty(memory_app: FastAPI) -> None:
    transport = ASGITransport(app=memory_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/memory/retrievals")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio()
async def test_create_pattern_calls_writer(memory_app: FastAPI) -> None:
    transport = ASGITransport(app=memory_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/patterns",
            json={
                "title": "SOL regime miss",
                "content": "Flagged as regime misclassification example.",
                "category": "regime_misclassification",
                "tags": ["review", "sol"],
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert "patternId" in body
    memory_app.state.rag_writer.write_pattern.assert_awaited_once()


@pytest.mark.asyncio()
async def test_tag_signal(memory_app: FastAPI) -> None:
    transport = ASGITransport(app=memory_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/memory/signal-tags",
            json={
                "signalId": "sig-123",
                "tags": ["regime_misclassification"],
                "note": "SOL trade retrospective",
            },
        )
    assert response.status_code == 200
    assert response.json()["ok"] is True
