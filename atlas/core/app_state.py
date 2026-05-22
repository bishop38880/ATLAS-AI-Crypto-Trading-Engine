"""Typed FastAPI application state for ATLAS (canonical holder + lifespan wiring).

Legacy handlers still read ``request.app.state.redis`` etc.; lifespan duplicates
those attributes from ``AtlasAppState`` for backward compatibility.
"""

from __future__ import annotations

import asyncio
from typing import Any

import asyncpg
import httpx
from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from atlas.core.llm_client import LocalLLMClient
from atlas.core.redis_resilience import SwappableRedisProxy
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.writer import RAGWriter


class AtlasAppState(BaseModel):
    """Process-wide singletons created during FastAPI lifespan."""

    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=False)

    redis: Redis | SwappableRedisProxy
    db_pool: asyncpg.Pool | None = None
    local_llm: LocalLLMClient | None = None
    atlas_llm_http: httpx.AsyncClient | None = None
    dashboard_market_http: httpx.AsyncClient | None = None
    embedding_service: EmbeddingService | None = None
    rag_writer: RAGWriter | None = None
    qdrant_client: AsyncQdrantClient | None = None
    paper_trade_executor_task: asyncio.Task[Any] | None = None
    paper_trade_http_client: httpx.AsyncClient | None = None
    startup_report: Any | None = None
    redis_runtime: Any | None = None
