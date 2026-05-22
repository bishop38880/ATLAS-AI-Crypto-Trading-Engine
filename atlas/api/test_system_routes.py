"""Tests for /api/system control routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.system import router as system_router


@pytest.fixture()
def app() -> FastAPI:
    _app = FastAPI()
    _app.state.redis = MagicMock()
    _app.state.autonomous_runner = None
    _app.state.embedding_service = MagicMock()
    _app.state.rag_writer = None
    _app.include_router(system_router)
    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.anyio
async def test_autonomous_engine_status_reflects_runner(app: FastAPI, client: AsyncClient) -> None:
    """GET reports stopped when no runner; running when runner attached."""
    response = await client.get("/api/system/autonomous-engine")
    assert response.status_code == 200
    assert response.json() == {"running": False}

    app.state.autonomous_runner = object()
    response_running = await client.get("/api/system/autonomous-engine")
    assert response_running.status_code == 200
    assert response_running.json() == {"running": True}


@pytest.mark.anyio
async def test_start_autonomous_engine_invokes_runner(client: AsyncClient) -> None:
    """First POST starts AutonomousRAGAnalysisRunner and returns started."""
    with (
        patch(
            "atlas.api.routes.system.build_rag_query_engine",
            new_callable=AsyncMock,
        ) as mock_build,
        patch("atlas.api.routes.system.AutonomousRAGAnalysisRunner") as mock_runner_class,
    ):
        mock_build.return_value = None
        instance = MagicMock()
        instance.start = AsyncMock()
        mock_runner_class.return_value = instance

        response = await client.post("/api/system/start-autonomous-engine")

    assert response.status_code == 200
    assert response.json()["status"] == "started"
    instance.start.assert_awaited_once()


@pytest.mark.anyio
async def test_start_autonomous_engine_idempotent(app: FastAPI, client: AsyncClient) -> None:
    """Second POST returns already_running when runner is present."""
    app.state.autonomous_runner = object()

    response = await client.post("/api/system/start-autonomous-engine")

    assert response.status_code == 200
    assert response.json()["status"] == "already_running"


@pytest.mark.anyio
async def test_start_all_arms_hydra_and_starts_runner(client: AsyncClient) -> None:
    """One POST arms HYDRA and starts the autonomous analysis loop."""
    with (
        patch("atlas.api.routes.system.subprocess.Popen") as mock_popen,
        patch(
            "atlas.api.routes.system.build_rag_query_engine",
            new_callable=AsyncMock,
        ) as mock_build,
        patch("atlas.api.routes.system.AutonomousRAGAnalysisRunner") as mock_runner_class,
    ):
        mock_build.return_value = None
        instance = MagicMock()
        instance.start = AsyncMock()
        mock_runner_class.return_value = instance

        response = await client.post("/api/system/start-all")

    assert response.status_code == 200
    assert response.json() == {
        "status": "started",
        "hydra_status": "success",
        "engine_status": "started",
    }
    mock_popen.assert_called_once()
    instance.start.assert_awaited_once()


@pytest.mark.anyio
async def test_start_lmstudio_rag_analysis_requires_lmstudio(
    client: AsyncClient,
) -> None:
    """LM Studio route refuses to start when the local server is unavailable."""
    with patch(
        "atlas.api.routes.system._check_lmstudio_available",
        new_callable=AsyncMock,
    ) as mock_check:
        mock_check.return_value = False

        response = await client.post("/api/system/start-lmstudio-rag-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "lmstudio_unavailable"
    assert body["provider"] == "lmstudio"


@pytest.mark.anyio
async def test_start_lmstudio_rag_analysis_invokes_runner(
    client: AsyncClient,
) -> None:
    """LM Studio route starts the autonomous RAG runner when available."""
    with (
        patch(
            "atlas.api.routes.system._check_lmstudio_available",
            new_callable=AsyncMock,
        ) as mock_check,
        patch(
            "atlas.api.routes.system.build_rag_query_engine",
            new_callable=AsyncMock,
        ) as mock_build,
        patch("atlas.api.routes.system.AutonomousRAGAnalysisRunner") as mock_runner_class,
    ):
        mock_check.return_value = True
        mock_build.return_value = None
        instance = MagicMock()
        instance.start = AsyncMock()
        mock_runner_class.return_value = instance

        response = await client.post("/api/system/start-lmstudio-rag-analysis")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert body["provider"] == "lmstudio"
    instance.start.assert_awaited_once()
