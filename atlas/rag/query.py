"""RAG Query Engine — Qdrant-based vector similarity with freshness decay.

Provides ``RAGQueryEngine`` for semantic retrieval of historical contexts
from Qdrant (primary) with LanceDB (hot standby). Supports adaptive
retrieval depth and exponential time-decay re-ranking.

Architecture:
    - Qdrant returns **similarity** (higher is better).
    - LanceDB returns **distance** (lower is better) — converted before decay.
    - All LanceDB calls wrapped in ``asyncio.to_thread()``.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import lancedb
import msgspec
from loguru import logger
from pydantic import BaseModel, ConfigDict
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Condition,
    FieldCondition,
    Filter,
    MatchValue,
    SearchParams,
)

from atlas.rag.embedding_service import EmbeddingService
from atlas.shared.config import PolarisSettings

SIGNAL_MEMORY_COLLECTION = "rag_signal_memory"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RetrievalDepth(str, Enum):
    """Controls search breadth — maps to top_k and hnsw_ef."""

    SHALLOW = "shallow"
    DEFAULT = "default"
    DEEP = "deep"


class ScoredDocument(BaseModel):
    """A document with similarity score and optional freshness-adjusted score.

    Attributes:
        document_id: Unique identifier for the document.
        similarity_score: Cosine similarity in [0, 1] (NOT distance).
        timestamp: UTC datetime when the document was created.
        final_score: Freshness-adjusted score (populated by decay).
        payload: Arbitrary metadata from the vector store.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str
    similarity_score: float
    timestamp: datetime
    final_score: float = 0.0
    payload: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# RAG Query Engine
# ---------------------------------------------------------------------------


class RAGQueryEngine:
    """Qdrant-based RAG query engine with LanceDB fallback.

    Supports adaptive retrieval depth and exponential freshness decay
    for time-sensitive re-ranking of retrieved contexts.

    Args:
        settings: PolarisSettings instance.
        qdrant_client: Async Qdrant client.
        lancedb_conn: LanceDB connection (sync — calls via to_thread).
        embedding_service: Mistral embedding service (1024-dim).
        collection: Qdrant collection name.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        qdrant_client: AsyncQdrantClient,
        lancedb_conn: lancedb.DBConnection,
        embedding_service: EmbeddingService,
        collection: str = SIGNAL_MEMORY_COLLECTION,
    ) -> None:
        """Initialize RAGQueryEngine with backends and config."""
        self._settings = settings
        self._qdrant = qdrant_client
        self._lancedb = lancedb_conn
        self._embed = embedding_service
        self._collection = collection

    async def aclose(self) -> None:
        """Close the Qdrant client (call on application shutdown)."""
        await self._qdrant.close()

    # ── Public API ────────────────────────────────────────────────────

    async def find_similar_contexts(
        self,
        query: str,
        retrieval_depth: RetrievalDepth = RetrievalDepth.DEFAULT,
        qdrant_filter: Filter | None = None,
        include_archived: bool = False,
    ) -> list[ScoredDocument]:
        """Find semantically similar contexts with freshness decay.

        Args:
            query: Natural-language query to embed and search.
            retrieval_depth: Controls top_k and hnsw_ef.
            qdrant_filter: Optional Qdrant filter for metadata.
            include_archived: If False, exclude archived documents.

        Returns:
            Scored documents sorted by freshness-adjusted score (desc).
        """
        embedding = await self._embed.embed(query)
        limit, ef = self._depth_to_params(retrieval_depth)

        effective_filter = _merge_archived_filter(
            qdrant_filter, include_archived,
        )
        documents = await self._search_qdrant(
            embedding, limit, ef, effective_filter,
        )

        if not documents:
            logger.warning("Qdrant returned 0 results, falling back to LanceDB")
            documents = await self._search_lancedb(
                embedding, limit, retrieval_depth,
            )

        now = datetime.now(timezone.utc)
        return self._apply_freshness_decay(documents, now)

    # ── Depth mapping ─────────────────────────────────────────────────

    def _depth_to_params(self, depth: RetrievalDepth) -> tuple[int, int]:
        """Return (top_k, hnsw_ef) for a given retrieval depth.

        Args:
            depth: The retrieval depth enum value.

        Returns:
            Tuple of (top_k limit, hnsw_ef search parameter).
        """
        s = self._settings
        if depth == RetrievalDepth.SHALLOW:
            return s.rag_shallow_top_k, s.rag_hnsw_ef_shallow
        if depth == RetrievalDepth.DEEP:
            return s.rag_deep_top_k, s.rag_hnsw_ef_deep
        return s.rag_default_top_k, s.rag_hnsw_ef_default

    # ── Freshness decay ───────────────────────────────────────────────

    def _apply_freshness_decay(
        self,
        documents: list[ScoredDocument],
        now: datetime,
    ) -> list[ScoredDocument]:
        """Re-rank documents by time-decayed similarity.

        final_score = similarity_score * exp(-lambda_per_hour * age_hours)

        Guards:
          - If freshness disabled, returns input unchanged.
          - Clock skew (age < 0) clamped to 0 — future docs get weight 1.0.
          - Very old documents (age > 1 week) clamped at 1-week weight.

        Args:
            documents: Scored documents with similarity_score set.
            now: Current UTC datetime for age calculation.

        Returns:
            New documents with final_score populated, sorted descending.
        """
        if not self._settings.rag_freshness_enabled:
            return [
                d.model_copy(update={"final_score": d.similarity_score})
                for d in documents
            ]

        lam = self._settings.rag_freshness_lambda_per_hour
        one_week_hours = 168.0
        scored: list[ScoredDocument] = []

        for doc in documents:
            age = (now - doc.timestamp).total_seconds() / 3600.0
            age_clamped = max(0.0, min(age, one_week_hours))
            weight = math.exp(-lam * age_clamped)
            final = doc.similarity_score * weight
            scored.append(doc.model_copy(update={"final_score": final}))

        return sorted(scored, key=lambda d: d.final_score, reverse=True)

    # ── Distance → similarity ─────────────────────────────────────────

    def _lancedb_distance_to_similarity(self, distance: float) -> float:
        """Convert LanceDB cosine distance to similarity.

        For unit-normalised vectors, cosine_distance in [0, 2] where
        0 = identical, 2 = opposite. similarity = 1 - (distance / 2).

        Args:
            distance: LanceDB cosine distance value.

        Returns:
            Similarity score clamped to [0, 1].
        """
        return max(0.0, min(1.0, 1.0 - (distance / 2.0)))

    # ── Qdrant search ─────────────────────────────────────────────────

    async def _search_qdrant(
        self,
        embedding: list[float],
        limit: int,
        ef: int,
        qdrant_filter: Filter | None,
    ) -> list[ScoredDocument]:
        """Search Qdrant for similar vectors with HNSW ef control.

        Args:
            embedding: 1024-dim query vector.
            limit: Maximum results to return.
            ef: HNSW ef search parameter (higher = better recall).
            qdrant_filter: Optional metadata filter.

        Returns:
            List of ScoredDocument from Qdrant results.
        """
        try:
            results = await self._qdrant.search(  # type: ignore[attr-defined]
                collection_name=self._collection,
                query_vector=embedding,
                limit=limit,
                search_params=SearchParams(hnsw_ef=ef),
                with_payload=True,
                query_filter=qdrant_filter,
                timeout=5,
            )
            return self._parse_qdrant_results(results)
        except Exception as exc:
            logger.error("Qdrant search failed | error={}", str(exc))
            return []

    def _parse_qdrant_results(
        self,
        results: list[Any],
    ) -> list[ScoredDocument]:
        """Parse Qdrant ScoredPoint objects into ScoredDocuments.

        Args:
            results: Raw Qdrant search results.

        Returns:
            List of ScoredDocument with similarity_score set.
        """
        documents: list[ScoredDocument] = []
        for point in results:
            payload = point.payload or {}
            ts_raw = payload.get(
                "timestamp",
                payload.get("cycle_timestamp", payload.get("created_at")),
            )
            timestamp = _parse_timestamp(ts_raw)
            documents.append(ScoredDocument(
                document_id=str(point.id),
                similarity_score=float(point.score),
                timestamp=timestamp,
                payload=payload,
            ))
        return documents

    # ── LanceDB fallback ──────────────────────────────────────────────

    async def _search_lancedb(
        self,
        embedding: list[float],
        limit: int,
        retrieval_depth: RetrievalDepth,
    ) -> list[ScoredDocument]:
        """Search LanceDB as fallback — sync calls via asyncio.to_thread.

        Args:
            embedding: 1024-dim query vector.
            limit: Maximum results to return.
            retrieval_depth: Controls nprobes for IVF_PQ indexes.

        Returns:
            List of ScoredDocument with distance converted to similarity.
        """
        nprobes = 20 if retrieval_depth == RetrievalDepth.DEEP else 10

        def _sync_lance_search() -> list[dict[str, Any]]:
            table = self._lancedb.open_table(self._collection)
            return (
                table.search(embedding)
                .limit(limit)
                .nprobes(nprobes)  # type: ignore[attr-defined]
                .to_list()
            )

        try:
            lance_results = await asyncio.wait_for(
                asyncio.to_thread(_sync_lance_search),
                timeout=5.0,
            )
            return self._parse_lance_results(lance_results)
        except Exception as exc:
            logger.error("LanceDB search failed | error={}", str(exc))
            return []

    def _parse_lance_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[ScoredDocument]:
        """Parse LanceDB results, converting distance to similarity.

        Args:
            results: Raw LanceDB search results as dicts.

        Returns:
            List of ScoredDocument with similarity_score set.
        """
        documents: list[ScoredDocument] = []
        for row in results:
            payload = _lancedb_payload(row)
            distance = float(row.get("_distance", 0.0))
            similarity = self._lancedb_distance_to_similarity(distance)
            ts_raw = payload.get(
                "timestamp",
                payload.get("cycle_timestamp", payload.get("created_at")),
            )
            timestamp = _parse_timestamp(ts_raw)
            documents.append(ScoredDocument(
                document_id=str(row.get("id", payload.get("document_id", ""))),
                similarity_score=similarity,
                timestamp=timestamp,
                payload=payload,
            ))
        return documents


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_timestamp(raw: Any) -> datetime:
    """Parse a timestamp from various formats into UTC datetime.

    Args:
        raw: ISO string, float epoch, or datetime.

    Returns:
        UTC-aware datetime. Falls back to now() if unparseable.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _merge_archived_filter(
    existing: Filter | None,
    include_archived: bool,
) -> Filter | None:
    """Merge an ``archived != true`` condition into a Qdrant filter.

    When ``include_archived`` is True the original filter is returned
    unmodified.  Otherwise a ``FieldCondition`` excluding archived
    documents is prepended to the ``must`` list.

    Args:
        existing: Caller-supplied filter (may be None).
        include_archived: Skip the archived filter when True.

    Returns:
        A new or augmented Filter, or the original if unchanged.
    """
    if include_archived:
        return existing

    archived_condition = FieldCondition(
        key="archived",
        match=MatchValue(value=True),
    )

    if existing is None:
        return Filter(must_not=[archived_condition])

    must: list[Condition] = []
    if existing.must is not None:
        if isinstance(existing.must, list):
            must.extend(existing.must)
        else:
            must.append(existing.must)  # type: ignore[arg-type]
    must_not: list[Condition] = [archived_condition]
    if existing.must_not is not None:
        if isinstance(existing.must_not, list):
            must_not.extend(existing.must_not)
        else:
            must_not.append(existing.must_not)  # type: ignore[arg-type]
    return Filter(
        must=must or None,
        should=existing.should,
        must_not=must_not,
    )


def _lancedb_payload(row: dict[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            decoded = msgspec.json.decode(metadata.encode(), type=dict)
            return decoded if isinstance(decoded, dict) else {}
        except msgspec.DecodeError:
            return {}
    if isinstance(metadata, bytes):
        try:
            decoded = msgspec.json.decode(metadata, type=dict)
            return decoded if isinstance(decoded, dict) else {}
        except msgspec.DecodeError:
            return {}
    return {k: v for k, v in row.items() if k not in {"_distance", "vector"}}

