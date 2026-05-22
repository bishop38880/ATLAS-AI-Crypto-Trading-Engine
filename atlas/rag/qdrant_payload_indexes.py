"""Qdrant payload indexes for ``rag_signal_memory``.

Single source of truth for which payload fields receive keyword / scalar
indexes so equality filters stay fast when collections grow.

Extend these tuples when adding new ``FieldCondition`` filters in
``atlas.rag.pipeline`` or ``atlas.rag.query``.
"""

from __future__ import annotations

from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import PayloadSchemaType

RAG_SIGNAL_MEMORY_COLLECTION: str = "rag_signal_memory"

# Fields used in equality filters (`MatchValue`) — MUST be indexed for scale.
KEYWORD_INDEX_FIELDS: tuple[str, ...] = (
    "asset",
    "signal_decision",
    "archived",  # RAGQueryEngine excludes archived hits when present
)

# Integer payload fields indexed for sorting / filtering.
INTEGER_INDEX_FIELDS: tuple[str, ...] = (
    "signal_score",
)


def keyword_index_field_list() -> list[str]:
    """Return keyword-indexed payload field names (copy)."""
    return list(KEYWORD_INDEX_FIELDS)


async def ensure_rag_signal_memory_payload_indexes(
    client: AsyncQdrantClient,
    *,
    collection_name: str = RAG_SIGNAL_MEMORY_COLLECTION,
    timeout_seconds: int = 30,
) -> None:
    """Create payload indexes if missing — idempotent, logs only on problems.

    Duplicate-index errors from Qdrant are ignored.
    """
    for field_name in KEYWORD_INDEX_FIELDS:
        await _create_payload_index_if_needed(
            client,
            collection_name=collection_name,
            field_name=field_name,
            field_schema=PayloadSchemaType.KEYWORD,
            timeout_seconds=timeout_seconds,
        )
    for field_name in INTEGER_INDEX_FIELDS:
        await _create_payload_index_if_needed(
            client,
            collection_name=collection_name,
            field_name=field_name,
            field_schema=PayloadSchemaType.INTEGER,
            timeout_seconds=timeout_seconds,
        )


async def _create_payload_index_if_needed(
    client: AsyncQdrantClient,
    *,
    collection_name: str,
    field_name: str,
    field_schema: PayloadSchemaType,
    timeout_seconds: int,
) -> None:
    try:
        await client.create_payload_index(
            collection_name=collection_name,
            field_name=field_name,
            field_schema=field_schema,
            wait=True,
            timeout=timeout_seconds,
        )
        logger.info(
            "Qdrant payload index ready | collection={} | field={} | schema={}",
            collection_name,
            field_name,
            str(field_schema),
        )
    except UnexpectedResponse as exc:
        raw_body = getattr(exc, "content", b"") or b""
        if isinstance(raw_body, bytes):
            lowered = raw_body.decode(errors="replace").lower()
        else:
            lowered = str(raw_body).lower()
        if not lowered:
            lowered = str(exc).lower()
        if "already exists" in lowered or "duplicate" in lowered:
            logger.debug(
                "Qdrant payload index skip (already present) | field={}",
                field_name,
            )
            return
        logger.warning(
            "Qdrant payload index failed | field={} | error={}",
            field_name,
            str(exc),
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "already exists" in msg or "duplicate" in msg:
            logger.debug(
                "Qdrant payload index skip (already present) | field={}",
                field_name,
            )
            return
        logger.warning(
            "Qdrant payload index failed | field={} | error={}",
            field_name,
            str(exc),
        )
