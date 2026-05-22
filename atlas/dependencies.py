"""Centralised FastAPI dependencies for ATLAS (re-exports)."""

from __future__ import annotations

from atlas.core.dependencies import (
    get_atlas_app_state,
    get_db_pool,
    get_embedding_service,
    get_local_llm,
    get_redis,
)

__all__ = [
    "get_atlas_app_state",
    "get_db_pool",
    "get_embedding_service",
    "get_local_llm",
    "get_redis",
]
