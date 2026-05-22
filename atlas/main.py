"""Compatibility import for ``from atlas.main import app``.

The canonical ASGI application lives in ``backend.main`` so lifespan wiring, Prometheus
mounts, and typed ``AtlasAppState`` stay in one place. Prefer ``uvicorn backend.main:app``.
"""

from __future__ import annotations

from backend.main import app

__all__ = ["app"]
