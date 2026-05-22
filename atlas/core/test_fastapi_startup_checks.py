"""Tests for ``run_fastapi_startup_checks`` (HTTP API lifespan alignment)."""

from __future__ import annotations

import pytest

from atlas.core.startup import (
    run_fastapi_startup_checks,
    validate_core_urls,
    validate_embedding_credentials,
)
from atlas.shared.config import PolarisSettings


@pytest.mark.asyncio
async def test_run_fastapi_startup_checks_with_pool_none_marks_postgres_degraded() -> None:
    import fakeredis.aioredis

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = PolarisSettings(
        embed_provider="lmstudio",
        redis_url="redis://localhost:6379/0",
        postgres_url="postgresql://x:y@localhost:5432/db",
        _env_file=None,
    )
    report = await run_fastapi_startup_checks(settings, redis_client, None)
    pg_step = next(s for s in report.steps if s.step == 3)
    assert pg_step.success is False
    assert "pool_unavailable" in pg_step.detail
    assert report.degraded_mode is True


def test_validate_embedding_allows_lmstudio_without_mistral_key() -> None:
    settings = PolarisSettings(embed_provider="lmstudio", _env_file=None)
    validate_embedding_credentials(settings)


def test_validate_core_urls_accepts_postgres_scheme_alias() -> None:
    settings = PolarisSettings(
        redis_url="redis://localhost:6379/0",
        postgres_url="postgres://user:pass@localhost/db",
        embed_provider="lmstudio",
        _env_file=None,
    )
    validate_core_urls(settings)


@pytest.mark.asyncio
async def test_hot_paths_signals_feed_and_omnibox_budget_integration() -> None:
    """Minimal ASGI smoke: signals feed + omnibox budget on a tiny app graph."""
    import fakeredis.aioredis
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from atlas.api.routes.omnibox import router as omnibox_router
    from atlas.api.routes.signals import router as signals_router
    from atlas.core.app_state import AtlasAppState
    from atlas.rag.embedding_service import EmbeddingService
    from atlas.shared.config import PolarisSettings

    redis_client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    settings = PolarisSettings(embed_provider="lmstudio", _env_file=None)
    embed = EmbeddingService(settings)

    app = FastAPI()
    app.state.redis = redis_client
    app.state.db_pool = None
    app.state.atlas = AtlasAppState(redis=redis_client, embedding_service=embed)

    app.include_router(signals_router)
    app.include_router(omnibox_router)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        feed = await client.get("/api/signals/feed")
        assert feed.status_code == 200
        budget = await client.get("/api/omnibox/budget")
        assert budget.status_code == 200
        assert budget.json()["sessionQueryLimit"] >= 1
