"""RAG Pipeline — persistent memory layer for all ATLAS agents.

S3-P5 canonical implementation. Writes and reads from:
  - **Qdrant** (primary) — async native HNSW, cosine; size = ``embed_dimensions``.
  - **LanceDB** (hot standby) — sync calls wrapped in ``asyncio.to_thread()``.
  - **Redis** — hot cache for latest context and query deduplication.
  - **PostgreSQL** — durable signal + outcome ledger via ``asyncpg``.

Architecture:
    Semantic-only retrieval via a single embedding model (dimension from
    ``PolarisSettings.embed_dimensions`` — must match Qdrant/LanceDB/PG).
    Keyword and hybrid retrieval are not used.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg
import msgspec
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)
from redis.asyncio import Redis

from atlas.models.signal import AgentResult, SignalOutput
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.qdrant_payload_indexes import ensure_rag_signal_memory_payload_indexes
from atlas.rag.rag_models import MemoryDocument, RAGTradeOutcome
from atlas.shared.config import PolarisSettings

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_REDIS_LATEST_TTL: int = 86_400  # 24 hours
_REDIS_QUERY_CACHE_TTL: int = 60  # 1 minute
_COLLECTION_NAME: str = "rag_signal_memory"
_LANCEDB_TABLE: str = "rag_signal_memory"

_CREATE_TABLE_SQL: str = """
CREATE TABLE IF NOT EXISTS rag_signal_memory (
    document_id    TEXT PRIMARY KEY,
    asset          TEXT NOT NULL,
    cycle_ts       TIMESTAMPTZ NOT NULL,
    signal_score   INTEGER,
    signal_json    JSONB,
    outcome_json   JSONB,
    created_at     TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_IDX_ASSET: str = (
    "CREATE INDEX IF NOT EXISTS idx_rag_asset "
    "ON rag_signal_memory(asset);"
)

_CREATE_IDX_TS: str = (
    "CREATE INDEX IF NOT EXISTS idx_rag_ts "
    "ON rag_signal_memory(cycle_ts DESC);"
)


# ---------------------------------------------------------------------------
# RAGPipeline
# ---------------------------------------------------------------------------


class RAGPipeline:
    """Persistent memory layer beneath all ATLAS agents.

    Provides write and read paths for signal memory. Agents do NOT
    manage their own memory — the orchestrator calls this pipeline
    automatically on every analysis cycle.

    Args:
        settings: PolarisSettings instance.
        redis_client: ``redis.asyncio.Redis`` connection.
        asyncpg_pool: Pre-created asyncpg connection pool.
        qdrant_client: Async Qdrant client.
        lancedb_conn: LanceDB connection (sync — wrapped).
        embedding_service: Embedding service (``embed_dimensions`` from settings).
        top_k: Default number of results for queries.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: Redis,  # type: ignore[type-arg]
        asyncpg_pool: asyncpg.Pool,
        qdrant_client: AsyncQdrantClient,
        lancedb_conn: Any,
        embedding_service: EmbeddingService,
        top_k: int = 10,
    ) -> None:
        """Initialize RAGPipeline with all backend dependencies."""
        self._settings = settings
        self._redis = redis_client
        self._pool = asyncpg_pool
        self._qdrant = qdrant_client
        self._lancedb = lancedb_conn
        self._embed = embedding_service
        self._top_k = top_k
        self._bootstrapped = False
        self._vector_dim = int(settings.embed_dimensions)

    # ── Bootstrap ─────────────────────────────────────────────────────

    async def bootstrap(self) -> None:
        """Idempotent bootstrap: Qdrant collection + LanceDB table + PG schema."""
        if self._bootstrapped:
            return
        await self._ensure_qdrant_collection()
        await asyncio.to_thread(self._ensure_lancedb_table)
        await self._ensure_pg_schema()
        self._bootstrapped = True

    async def warm_up(self) -> None:
        """Application startup hook (PolarisStartup step 7) — same as ``bootstrap``."""
        await self.bootstrap()

    async def _ensure_qdrant_collection(self) -> None:
        """Create Qdrant collection if it does not exist."""
        try:
            collections = await asyncio.wait_for(
                self._qdrant.get_collections(), timeout=10,
            )
            names = [c.name for c in collections.collections]
            if _COLLECTION_NAME not in names:
                await asyncio.wait_for(
                    self._qdrant.create_collection(
                        collection_name=_COLLECTION_NAME,
                        vectors_config=VectorParams(
                            size=self._vector_dim,
                            distance=Distance.COSINE,
                        ),
                    ),
                    timeout=10,
                )
                logger.info("Created Qdrant collection | name={}", _COLLECTION_NAME)
        except Exception as exc:
            logger.error("Qdrant bootstrap failed | error={}", str(exc))
            return
        await self._ensure_qdrant_payload_indexes()

    async def _ensure_qdrant_payload_indexes(self) -> None:
        """Ensure keyword / scalar payload indexes exist for equality filters."""
        try:
            await ensure_rag_signal_memory_payload_indexes(
                self._qdrant,
                collection_name=_COLLECTION_NAME,
            )
        except Exception as exc:
            logger.warning(
                "Qdrant payload index pass failed | error={}",
                str(exc),
            )

    def _ensure_lancedb_table(self) -> None:
        """Create LanceDB table if not present (sync — called via to_thread)."""
        try:
            existing = self._lancedb.table_names()
            if _LANCEDB_TABLE not in existing:
                import pyarrow as pa

                schema = pa.schema([
                    pa.field("id", pa.string()),
                    pa.field("vector", pa.list_(pa.float32(), self._vector_dim)),
                    pa.field("metadata", pa.string()),
                ])
                self._lancedb.create_table(_LANCEDB_TABLE, schema=schema)
                logger.info("Created LanceDB table | name={}", _LANCEDB_TABLE)
        except Exception as exc:
            logger.error("LanceDB bootstrap failed | error={}", str(exc))

    async def _ensure_pg_schema(self) -> None:
        """Create rag_signal_memory table + indexes if missing."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_CREATE_TABLE_SQL)
                await conn.execute(_CREATE_IDX_ASSET)
                await conn.execute(_CREATE_IDX_TS)
        except Exception as exc:
            logger.error("PostgreSQL bootstrap failed | error={}", str(exc))

    # ── Write: Signal Memory ─────────────────────────────────────────

    async def write_signal_memory(
        self,
        signal: SignalOutput,
        asset: str,
        agent_results: list[AgentResult],
        cycle_timestamp: datetime,
    ) -> str:
        """Persist a signal analysis cycle to all backends.

        Returns:
            The generated document_id (UUID4).
        """
        doc = _build_memory_document(signal, asset, agent_results, cycle_timestamp)
        vector = await self._embed_text(doc.text_content)
        await asyncio.gather(
            self._write_qdrant(doc, vector),
            self._write_lancedb(doc, vector),
            self._write_redis_latest(doc),
            self._write_pg(doc),
        )
        logger.info("Wrote signal memory | asset={} | doc_id={}", asset, doc.document_id)
        return doc.document_id

    async def _write_qdrant(self, doc: MemoryDocument, vector: list[float]) -> None:
        """Upsert one point into Qdrant. Logs on failure, never raises."""
        try:
            payload = _doc_to_payload(doc)
            point = PointStruct(
                id=doc.document_id,
                vector=vector,
                payload=payload,
            )
            await self._qdrant.upsert(
                collection_name=_COLLECTION_NAME,
                points=[point],
                timeout=5,
            )
        except Exception as exc:
            logger.error("Qdrant write failed | error={}", str(exc))

    async def _write_lancedb(self, doc: MemoryDocument, vector: list[float]) -> None:
        """Append one record to LanceDB (sync — wrapped in to_thread)."""
        record = {
            "id": doc.document_id,
            "vector": vector,
            "metadata": msgspec.json.encode(
                _doc_to_payload(doc),
            ).decode(),
        }
        try:
            await asyncio.to_thread(self._lancedb_add_record, record)
        except Exception as exc:
            logger.error("LanceDB write failed | error={}", str(exc))

    def _lancedb_add_record(self, record: dict[str, Any]) -> None:
        """Sync helper — called via asyncio.to_thread."""
        table = self._lancedb.open_table(_LANCEDB_TABLE)
        table.add([record])

    async def _write_redis_latest(self, doc: MemoryDocument) -> None:
        """Cache latest signal summary in Redis (TTL 24h)."""
        key = f"rag:{doc.asset}:latest"
        payload = _doc_to_payload(doc)
        try:
            encoded = msgspec.json.encode(payload)
            await asyncio.wait_for(
                self._redis.setex(key, _REDIS_LATEST_TTL, encoded),
                timeout=2,
            )
        except Exception as exc:
            logger.error("Redis latest-write failed | error={}", str(exc))

    async def _write_pg(self, doc: MemoryDocument) -> None:
        """Insert row into rag_signal_memory via asyncpg."""
        signal_json = msgspec.json.encode(
            _doc_to_payload(doc),
        ).decode()
        try:
            await self._pool.execute(
                """
                INSERT INTO rag_signal_memory
                    (document_id, asset, cycle_ts, signal_score, signal_json)
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (document_id) DO NOTHING
                """,
                doc.document_id,
                doc.asset,
                doc.cycle_timestamp,
                doc.signal_score,
                signal_json,
                timeout=5.0,
            )
        except Exception as exc:
            logger.error("PostgreSQL write failed | error={}", str(exc))

    # ── Read: Query Context ──────────────────────────────────────────

    async def query_context(
        self,
        query_text: str,
        asset: str,
        top_n: int = 5,
    ) -> list[MemoryDocument]:
        """Retrieve similar historical contexts for an asset.

        Steps:
            1. Check Redis query cache (SHA256 hash of normalised query).
            2. Embed query → 1024-dim vector.
            3. Try Qdrant; on failure fall through to LanceDB.
            4. Cache result in Redis (TTL 60s).

        Returns:
            List of MemoryDocument sorted by similarity (desc).
        """
        query_hash = _compute_query_hash(query_text)
        cache_key = f"rag:{asset}:context_cache:{query_hash}"

        cached = await self._read_query_cache(cache_key)
        if cached is not None:
            return cached

        vector = await self._embed_text(query_text)
        documents = await self._query_qdrant(vector, asset, top_n)

        if not documents:
            documents = await self._query_lancedb(vector, asset, top_n)

        await self._write_query_cache(cache_key, documents)
        return documents

    async def _read_query_cache(
        self,
        key: str,
    ) -> list[MemoryDocument] | None:
        """Read cached query results from Redis."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get(key), timeout=2,
            )
            if raw is None:
                return None
            payloads = msgspec.json.decode(raw)
            return [_payload_to_doc(p) for p in payloads]
        except Exception as exc:
            logger.warning("Redis query cache read failed | error={}", str(exc))
            return None

    async def _write_query_cache(
        self,
        key: str,
        documents: list[MemoryDocument],
    ) -> None:
        """Cache query results in Redis with short TTL."""
        try:
            payloads = [_doc_to_payload(d) for d in documents]
            encoded = msgspec.json.encode(payloads)
            await asyncio.wait_for(
                self._redis.setex(key, _REDIS_QUERY_CACHE_TTL, encoded),
                timeout=2,
            )
        except Exception as exc:
            logger.warning("Redis query cache write failed | error={}", str(exc))

    async def _query_qdrant(
        self,
        vector: list[float],
        asset: str,
        top_n: int,
    ) -> list[MemoryDocument]:
        """Search Qdrant for similar documents filtered by asset."""
        asset_filter = Filter(
            must=[FieldCondition(key="asset", match=MatchValue(value=asset))],
        )
        try:
            results = await self._qdrant.search( # type: ignore[attr-defined]
                collection_name=_COLLECTION_NAME,
                query_vector=vector,
                query_filter=asset_filter,
                limit=top_n,
                with_payload=True,
                timeout=5,
            )
            return [_qdrant_hit_to_doc(hit) for hit in results]
        except Exception as exc:
            logger.warning("Qdrant unavailable | asset={} | fallback={} | error={}", asset, "lancedb", str(exc))
            return []

    async def _query_lancedb(
        self,
        vector: list[float],
        asset: str,
        top_n: int,
    ) -> list[MemoryDocument]:
        """LanceDB fallback — sync calls via asyncio.to_thread."""
        try:
            results = await asyncio.to_thread(
                self._lancedb_search, vector, asset, top_n,
            )
            return [_lance_row_to_doc(row) for row in results]
        except Exception as exc:
            logger.critical("Both Qdrant and LanceDB failed | error={}", str(exc))
            return []

    def _lancedb_search(
        self,
        vector: list[float],
        asset: str,
        top_n: int,
    ) -> list[dict[str, Any]]:
        """Sync LanceDB search — over-fetch nearest vectors, keep matching asset."""
        table = self._lancedb.open_table(_LANCEDB_TABLE)
        over_fetch = min(max(top_n * 10, top_n), 100)
        rows = table.search(vector).limit(over_fetch).to_list()
        filtered: list[dict[str, Any]] = []
        for row in rows:
            try:
                if _lance_row_to_doc(row).asset != asset:
                    continue
            except Exception:
                continue
            filtered.append(row)
            if len(filtered) >= top_n:
                break
        return filtered

    # ── Write: Outcome ───────────────────────────────────────────────

    async def write_outcome_to_memory(
        self,
        signal_id: str,
        outcome: RAGTradeOutcome,
    ) -> None:
        """Enrich an existing signal memory with trade outcome.

        Updates PostgreSQL, re-embeds the enriched document, and
        upserts to Qdrant + LanceDB.
        """
        await self._update_pg_outcome(signal_id, outcome)
        enriched_doc = await self._rebuild_enriched_doc(signal_id, outcome)
        if enriched_doc is None:
            return
        vector = await self._embed_text(enriched_doc.text_content)
        await self._write_qdrant(enriched_doc, vector)
        await self._write_lancedb(enriched_doc, vector)
        logger.info("Wrote outcome to memory | signal_id={}", signal_id)

    async def _update_pg_outcome(
        self,
        signal_id: str,
        outcome: RAGTradeOutcome,
    ) -> None:
        """Update the PostgreSQL row with outcome JSON."""
        outcome_json = msgspec.json.encode(
            outcome.model_dump(mode="json"),
        ).decode()
        try:
            await self._pool.execute(
                """
                UPDATE rag_signal_memory
                SET outcome_json = $1::jsonb
                WHERE document_id = $2
                """,
                outcome_json,
                signal_id,
                timeout=5.0,
            )
        except Exception as exc:
            logger.error("PG outcome update failed | error={}", str(exc))

    async def _rebuild_enriched_doc(
        self,
        signal_id: str,
        outcome: RAGTradeOutcome,
    ) -> MemoryDocument | None:
        """Rebuild a MemoryDocument with outcome for re-embedding."""
        try:
            row = await self._pool.fetchrow(
                "SELECT signal_json, asset, cycle_ts, signal_score "
                "FROM rag_signal_memory WHERE document_id = $1",
                signal_id,
                timeout=5.0,
            )
            if row is None:
                logger.warning("Signal not found for re-embed | signal_id={}", signal_id)
                return None
            return _rebuild_doc_from_row(row, signal_id, outcome)
        except Exception as exc:
            logger.error("Rebuild enriched doc failed | error={}", str(exc))
            return None

    # ── Embedding ────────────────────────────────────────────────────

    async def _embed_text(self, text: str) -> list[float]:
        """Embed text via Mistral. Returns zero-vector on failure."""
        try:
            return await self._embed.embed(text)
        except Exception as exc:
            logger.error("Embedding failed, returning zero vector | error={}", str(exc))
            return [0.0] * self._vector_dim


# ---------------------------------------------------------------------------
# Pure helpers (no I/O, no state)
# ---------------------------------------------------------------------------


def _compute_query_hash(query: str) -> str:
    """SHA256 first 16 hex of normalised query."""
    normalised = query.lower().strip()
    return hashlib.sha256(normalised.encode()).hexdigest()[:16]


def _build_memory_document(
    signal: SignalOutput,
    asset: str,
    agent_results: list[AgentResult],
    cycle_timestamp: datetime,
) -> MemoryDocument:
    """Construct a MemoryDocument from a signal and agent results."""
    explanations = [r.explanation for r in agent_results if r.explanation]
    text_content = _build_text_content(signal, explanations)
    cat_scores: dict[str, float] = {}
    if signal.category_scores:
        cat_scores = {
            k: float(v)
            for k, v in signal.category_scores.model_dump().items()
        }
    return MemoryDocument(
        document_id=str(uuid4()),
        asset=asset,
        cycle_timestamp=cycle_timestamp,
        signal_score=signal.score,
        signal_decision=signal.decision.value,
        category_scores=cat_scores,
        agent_explanations=explanations,
        text_content=text_content,
    )


def _build_text_content(
    signal: SignalOutput,
    explanations: list[str],
) -> str:
    """Build human-readable text for embedding."""
    parts = [
        f"Asset: {signal.asset}",
        f"Decision: {signal.decision.value}",
        f"Score: {signal.score}/100",
    ]
    if signal.reasoning_summary:
        parts.append(f"Reasoning: {signal.reasoning_summary}")
    if explanations:
        parts.append("Agents: " + "; ".join(explanations[:5]))
    return " | ".join(parts)


def _doc_to_payload(doc: MemoryDocument) -> dict[str, Any]:
    """Serialise MemoryDocument to a dict for storage."""
    return doc.model_dump(mode="json")


def _payload_to_doc(payload: dict[str, Any]) -> MemoryDocument:
    """Deserialise a dict payload back to MemoryDocument."""
    return MemoryDocument.model_validate(payload)


def _qdrant_hit_to_doc(hit: Any) -> MemoryDocument:
    """Convert a Qdrant ScoredPoint to MemoryDocument."""
    payload = hit.payload or {}
    return _payload_to_doc(payload)


def _lance_row_to_doc(row: dict[str, Any]) -> MemoryDocument:
    """Convert a LanceDB result row to MemoryDocument."""
    metadata_raw = row.get("metadata", "{}")
    payload = msgspec.json.decode(
        metadata_raw.encode() if isinstance(metadata_raw, str) else metadata_raw,
    )
    return _payload_to_doc(payload)


def _rebuild_doc_from_row(
    row: asyncpg.Record,
    signal_id: str,
    outcome: RAGTradeOutcome,
) -> MemoryDocument:
    """Rebuild MemoryDocument from PG row + outcome."""
    signal_data = msgspec.json.decode(row["signal_json"].encode())
    text_parts = [f"Signal: {signal_id}"]
    if outcome.pnl_pct is not None:
        text_parts.append(f"PnL: {outcome.pnl_pct:.2f}%")
    if outcome.market_moved_as:
        text_parts.append(f"Market: {outcome.market_moved_as}")
    original_text = signal_data.get("text_content", "")
    enriched_text = original_text + " | Outcome: " + " ".join(text_parts)
    return MemoryDocument(
        document_id=signal_id,
        asset=row["asset"],
        cycle_timestamp=row["cycle_ts"],
        signal_score=row["signal_score"] or 0,
        signal_decision=signal_data.get("signal_decision", "Hold"),
        category_scores=signal_data.get("category_scores", {}),
        agent_explanations=signal_data.get("agent_explanations", []),
        outcome=outcome,
        text_content=enriched_text,
    )
