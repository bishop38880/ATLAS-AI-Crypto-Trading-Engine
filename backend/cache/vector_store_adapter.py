"""Vector retrieval router — Qdrant primary, LanceDB hot standby, Redis publish hook."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final

import lancedb  # type: ignore[import-untyped]
import msgspec
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import SearchParams

from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.query import SIGNAL_MEMORY_COLLECTION
from atlas.shared.config import PolarisSettings
from backend.schemas.atlas_signals import (
    CURRENT_INTELLIGENCE_SCHEMA,
    IntelligenceRedisChannels,
    RagContextBundleV23,
    RagRetrievalChunkV23,
)


class VectorStoreAdapter:
    """Hybrid vector retrieval with deterministic keyword filtering.

    Hybrid mode = dense embedding search plus optional keyword pass on payload text.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        qdrant_client: AsyncQdrantClient | None,
        lancedb_connection: lancedb.DBConnection | None,
        embedding_service: EmbeddingService,
        collection: str = SIGNAL_MEMORY_COLLECTION,
    ) -> None:
        assert settings is not None, "PolarisSettings required"
        self._settings = settings
        self._qdrant = qdrant_client
        self._lancedb = lancedb_connection
        self._embed = embedding_service
        self._collection = collection

    def _distance_to_similarity(self, distance: float) -> float:
        """Convert Lance cosine distance to similarity in [0, 1] (float allowed for similarity)."""
        return max(0.0, min(1.0, 1.0 - (distance / 2.0)))

    async def hybrid_search_context(
        self,
        asset: str,
        query: str,
        keyword: str | None,
        limit: int,
    ) -> RagContextBundleV23:
        """Retrieve memory contexts; fall back from Qdrant to LanceDB."""
        assert isinstance(asset, str) and len(asset) >= 1, "asset must be non-empty"
        assert isinstance(query, str) and len(query) >= 1, "query must be non-empty"
        assert limit > 0, "limit must be positive"

        embedding = await self._embed.embed(query)
        docs = await self._search_qdrant_dense(embedding, limit)
        backend_used = "qdrant"
        if not docs and self._lancedb is not None:
            docs = await self._search_lance_dense(embedding, limit)
            backend_used = "lancedb"

        if not docs:
            logger.warning(
                "vector_adapter_empty | asset={} | backend_attempted={}",
                asset,
                backend_used,
            )
            return RagContextBundleV23(
                query=query,
                keyword_filter=keyword,
                chunks=tuple(),
            )

        filtered = self._keyword_filter_rows(docs, keyword)
        chunks = self._to_chunks(filtered, backend_used=backend_used)
        return RagContextBundleV23(
            query=query,
            keyword_filter=keyword,
            chunks=chunks,
        )

    async def publish_intelligence_snapshot(
        self,
        redis: Any,
        payload_dict: dict[str, Any],
    ) -> None:
        """Publish dashboard intelligence and mirror a snapshot key (best-effort)."""
        channels = IntelligenceRedisChannels()
        body = msgspec.json.encode(payload_dict)
        await redis.publish(channels.pubsub_stream, body)
        snap_key = channels.snapshot_key
        await redis.set(snap_key, body, ex=86400)
        logger.info(
            "vector_adapter_pub_snapshot | channel={} | snapshot_key={} | schema={}",
            channels.pubsub_stream,
            snap_key,
            CURRENT_INTELLIGENCE_SCHEMA,
        )

    async def _search_qdrant_dense(
        self,
        embedding: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        if self._qdrant is None:
            logger.warning("vector_adapter_qdrant_missing | fallback=lancedb_or_empty")
            return []
        try:
            ef = self._settings.rag_hnsw_ef_default
            results = await self._qdrant.search(
                collection_name=self._collection,
                query_vector=embedding,
                limit=limit,
                search_params=SearchParams(hnsw_ef=ef),
                with_payload=True,
                timeout=5,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("vector_adapter_qdrant_search_failed | err={}", str(exc))
            return []

        out: list[dict[str, Any]] = []
        for point in results:
            payload = point.payload or {}
            out.append(
                {
                    "id": str(point.id),
                    "score": float(point.score),
                    "payload": payload,
                },
            )
        return out

    async def _search_lance_dense(
        self,
        embedding: list[float],
        limit: int,
    ) -> list[dict[str, Any]]:
        lance = self._lancedb
        if lance is None:
            return []

        collection_name = self._collection

        def _sync() -> list[dict[str, Any]]:
            table = lance.open_table(collection_name)
            return table.search(embedding).limit(limit).to_list()

        try:
            rows = await asyncio.wait_for(asyncio.to_thread(_sync), timeout=5.0)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("vector_adapter_lancedb_search_failed | err={}", str(exc))
            return []

        parsed: list[dict[str, Any]] = []
        for row in rows:
            payload = {k: v for k, v in row.items() if k not in {"_distance", "id"}}
            distance = float(row.get("_distance", 0.0))
            similarity = self._distance_to_similarity(distance)
            parsed.append(
                {
                    "id": str(row.get("id", "")),
                    "score": similarity,
                    "payload": payload,
                },
            )
        return parsed

    def _keyword_filter_rows(
        self,
        rows: list[dict[str, Any]],
        keyword: str | None,
    ) -> list[dict[str, Any]]:
        if not keyword:
            return rows
        needle = keyword.lower()
        kept: list[dict[str, Any]] = []
        for row in rows:
            payload = row.get("payload") or {}
            haystack_parts: list[str] = []
            for val in payload.values():
                if isinstance(val, str):
                    haystack_parts.append(val.lower())
            hay = " ".join(haystack_parts)
            if needle in hay:
                kept.append(row)
        if not kept:
            logger.info(
                "vector_adapter_keyword_miss | keyword={} | kept=all_unfiltered",
                keyword,
            )
            return rows
        return kept

    def _to_chunks(
        self,
        rows: list[dict[str, Any]],
        backend_used: str,
    ) -> tuple[RagRetrievalChunkV23, ...]:
        chunks: list[RagRetrievalChunkV23] = []
        backend_literal: Final = "lancedb" if backend_used == "lancedb" else "qdrant"
        for row in rows:
            payload = row.get("payload") or {}
            excerpt_parts: list[str] = []
            for key in ("summary", "text", "content", "reasoning_summary"):
                val = payload.get(key)
                if isinstance(val, str) and val.strip():
                    excerpt_parts.append(val.strip()[:280])
                    break
            excerpt = " | ".join(excerpt_parts) if excerpt_parts else ""

            ts_raw = payload.get("timestamp") or payload.get("cycle_timestamp")
            timestamp_millis = self._coerce_timestamp_millis(ts_raw)

            chunks.append(
                RagRetrievalChunkV23(
                    document_id=str(row.get("id", "unknown")),
                    similarity_score=float(row.get("score", 0.0)),
                    final_score=float(row.get("score", 0.0)),
                    timestamp_millis=timestamp_millis,
                    text_excerpt=excerpt,
                    backend=backend_literal,
                ),
            )
        return tuple(chunks)

    def _coerce_timestamp_millis(self, raw: Any) -> int:
        if isinstance(raw, datetime):
            dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        if isinstance(raw, (int, float)):
            # assume seconds if small, millis if large
            if raw > 1_000_000_000_000:
                return int(raw)
            return int(raw * 1000)
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1000)
            except ValueError:
                pass
        return int(datetime.now(timezone.utc).timestamp() * 1000)


async def open_lancedb_connection(settings: PolarisSettings) -> lancedb.DBConnection | None:
    """Open LanceDB (sync API) on a worker thread."""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(lancedb.connect, settings.lancedb_uri),
            timeout=5.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("vector_adapter_lancedb_open_failed | err={}", str(exc))
        return None
