"""Redis-backed telemetry for RAG retrieval — UI and post-mortem visibility.

Every pipeline RAG query appends a compact JSON event so operators can see
which historical vectors influenced the current cycle (scores, previews).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, cast
from uuid import uuid4

import msgspec
from loguru import logger
from redis.asyncio import Redis

# Most recent first, capped for memory bounds.
_REDIS_KEY: str = "polaris:rag:retrieval_events"
_MAX_EVENTS: int = 100


def _utc_iso() -> str:
    """Current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def build_retrieval_event_dict(
    *,
    asset: str,
    query_text: str,
    retrieval_depth: str,
    hit_count: int,
    hits: list[dict[str, Any]],
    error: str | None = None,
) -> dict[str, Any]:
    """Shape one telemetry record (JSON-serialisable)."""
    ts = _utc_iso()
    return {
        "id": str(uuid4()),
        "timestamp": ts,
        "asset": asset,
        "queryText": query_text,
        "retrievalDepth": retrieval_depth,
        "hitCount": hit_count,
        "hits": hits,
        "error": error,
    }


async def append_retrieval_event(
    redis: Redis,
    event: dict[str, Any],
) -> None:
    """Push one event to the capped Redis list (LPUSH + LTRIM).

    Fire-and-forget degrades silently on Redis failure — never blocks scoring.
    """
    try:
        payload = msgspec.json.encode(event)
        pipe = redis.pipeline()
        pipe.lpush(_REDIS_KEY, payload)
        pipe.ltrim(_REDIS_KEY, 0, _MAX_EVENTS - 1)
        await asyncio.wait_for(pipe.execute(), timeout=2.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "rag_retrieval_telemetry_failed | asset={} | err={}",
            event.get("asset", ""),
            str(exc),
        )


async def list_recent_retrieval_events(
    redis: Redis,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    """Return recent retrieval events (newest first), best-effort decode."""
    safe_limit = max(1, min(int(limit), _MAX_EVENTS))
    try:
        raw_result = await asyncio.wait_for(
            cast(
                Awaitable[list[Any]],
                redis.lrange(_REDIS_KEY, 0, safe_limit - 1),
            ),
            timeout=2.0,
        )
        raw_list: list[Any] = (
            list(raw_result)
            if raw_result is not None
            else []
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("rag_retrieval_telemetry_read_failed | err={}", str(exc))
        return []

    out: list[dict[str, Any]] = []
    for raw in raw_list:
        if raw is None:
            continue
        blob = raw if isinstance(raw, (bytes, bytearray)) else str(raw).encode()
        try:
            decoded = msgspec.json.decode(blob)
            if isinstance(decoded, dict):
                out.append(decoded)
        except msgspec.DecodeError:
            continue
    return out
