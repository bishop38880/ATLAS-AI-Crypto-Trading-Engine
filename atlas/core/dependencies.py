"""FastAPI dependencies for ATLAS core services."""

from __future__ import annotations

import asyncpg
from fastapi import Request
from redis.asyncio import Redis

from atlas.core.app_state import AtlasAppState
from atlas.core.llm_client import LocalLLMClient
from atlas.rag.embedding_service import EmbeddingService


def get_atlas_app_state(request: Request) -> AtlasAppState:
    """Return the typed lifespan container (canonical source after startup)."""
    state = getattr(request.app.state, "atlas", None)
    if not isinstance(state, AtlasAppState):
        raise RuntimeError("App state missing atlas container")
    return state


def get_redis(request: Request) -> Redis:
    """Redis singleton — prefers ``AtlasAppState``; falls back to legacy ``app.state.redis``."""
    atlas_state = getattr(request.app.state, "atlas", None)
    if isinstance(atlas_state, AtlasAppState):
        return atlas_state.redis
    client = getattr(request.app.state, "redis", None)
    if isinstance(client, Redis):
        return client
    raise RuntimeError("App state missing redis client")


def get_db_pool(request: Request) -> asyncpg.Pool | None:
    """Postgres pool (may be ``None`` when historical store is disabled)."""
    atlas_state = getattr(request.app.state, "atlas", None)
    if isinstance(atlas_state, AtlasAppState):
        return atlas_state.db_pool
    return getattr(request.app.state, "db_pool", None)


def get_embedding_service(request: Request) -> EmbeddingService:
    """Embedding service built at lifespan."""
    svc = get_atlas_app_state(request).embedding_service
    if svc is None:
        raise RuntimeError("App state missing embedding_service")
    return svc


def get_local_llm(request: Request) -> LocalLLMClient:
    """Return the application-scoped local LLM client (one httpx pool per process)."""
    client = get_atlas_app_state(request).local_llm
    if client is None:
        raise RuntimeError("App state missing local_llm client")
    return client
