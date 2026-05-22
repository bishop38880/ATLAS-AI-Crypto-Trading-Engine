"""Shared AsyncQdrantClient construction from PolarisSettings."""

from __future__ import annotations

from qdrant_client import AsyncQdrantClient

from atlas.shared.config import PolarisSettings


def resolve_qdrant_api_key(settings: PolarisSettings) -> str | None:
    """Return Qdrant API key when configured; None for local unsecured instances."""
    raw = settings.qdrant_api_key.get_secret_value().strip()
    if not raw:
        return None
    return raw


def create_async_qdrant_client(
    settings: PolarisSettings,
    *,
    timeout: float | int = 5,
) -> AsyncQdrantClient:
    """Build an AsyncQdrantClient using ``QDRANT_URL`` and optional ``QDRANT_API_KEY``."""
    api_key = resolve_qdrant_api_key(settings)
    if api_key is not None:
        return AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=api_key,
            timeout=timeout,
            check_compatibility=False,
        )
    return AsyncQdrantClient(
        url=settings.qdrant_url,
        timeout=timeout,
        check_compatibility=False,
    )
