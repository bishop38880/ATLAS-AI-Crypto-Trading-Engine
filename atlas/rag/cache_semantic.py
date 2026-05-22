"""Semantic Cache (Tier 2) for ATLAS RAG.

Uses Qdrant as the primary vector database for semantic similarity,
with LanceDB as a hot standby. Embeddings are 1024-dimensional.
"""

import asyncio
import time
import uuid

import lancedb
import pyarrow as pa
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, Range, VectorParams

from atlas.rag.cache_exact import CacheEntry
from atlas.rag.embedding_service import EmbeddingService

THRESHOLDS = {
    "default": 0.93,
    "sentiment": 0.90,
    "market_analysis": 0.95,
}

class SemanticCache:
    """Tier 2 cache using semantic similarity on 1024-dim embeddings."""

    def __init__(
        self,
        qdrant_client: AsyncQdrantClient,
        lancedb_conn: lancedb.DBConnection,
        embedding_service: EmbeddingService,
    ) -> None:
        """Initialize semantic cache with DB connections and embedder."""
        self._qdrant = qdrant_client
        self._lancedb = lancedb_conn
        self._embedder = embedding_service
        self._collection = "semantic_cache"

    async def initialize(self) -> None:
        """Create collections/tables if they don't exist."""
        try:
            if not await self._qdrant.collection_exists(self._collection):
                await self._qdrant.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
                )
        except Exception as e:
            logger.warning("Qdrant init failed: {}", e)

        def init_lancedb() -> None:
            if self._collection not in self._lancedb.list_tables():  # type: ignore
                schema = pa.schema([
                    pa.field("vector", pa.list_(pa.float32(), 1024)),
                    pa.field("id", pa.string()),
                    pa.field("prompt_text", pa.string()),
                    pa.field("response_text", pa.string()),
                    pa.field("provider", pa.string()),
                    pa.field("category", pa.string()),
                    pa.field("created_at", pa.float64()),
                    pa.field("expires_at", pa.float64())
                ])
                self._lancedb.create_table(self._collection, schema=schema)

        await asyncio.to_thread(init_lancedb)

    def _get_threshold(self, category: str) -> float:
        return THRESHOLDS.get(category, THRESHOLDS["default"])

    async def search(self, prompt: str, category: str) -> CacheEntry | None:
        """Search the semantic cache for a sufficiently similar prompt."""
        vector = await self._embedder.embed(prompt)
        threshold = self._get_threshold(category)
        now = time.time()

        try:
            results = await self._qdrant.query_points(
                collection_name=self._collection,
                query=vector,
                score_threshold=threshold,
                limit=1,
                query_filter=Filter(must=[
                    FieldCondition(key="expires_at", range=Range(gte=now)),
                    FieldCondition(key="category", match=MatchValue(value=category)),
                ]),
            )
            if results.points:
                payload = results.points[0].payload or {}
                return CacheEntry(
                    response=str(payload.get("response_text", "")),
                    created_at=float(payload.get("created_at", 0.0)),
                    provider=str(payload.get("provider", "")),
                )
        except Exception as e:
            logger.warning("Qdrant search failed, falling back to LanceDB: {}", e)
            return await asyncio.to_thread(self._lancedb_search, vector, category, threshold, now)
        return None

    def _lancedb_search(self, vector: list[float], cat: str, thresh: float, now: float) -> CacheEntry | None:
        table = self._lancedb.open_table(self._collection)
        results = table.search(vector, vector_column_name="vector").metric("cosine").where(  # type: ignore
            f"category = '{cat}' AND expires_at >= {now}"
        ).limit(1).to_list()
        
        if results:
            best = results[0]
            similarity = 1.0 - best["_distance"]
            if similarity >= thresh:
                return CacheEntry(
                    response=best["response_text"],
                    created_at=best["created_at"],
                    provider=best["provider"],
                )
        return None

    async def store(self, prompt: str, response: str, provider: str, category: str, ttl: int) -> None:
        """Store a response in both Qdrant and LanceDB."""
        vector = await self._embedder.embed(prompt)
        now = time.time()
        payload = {
            "prompt_text": prompt, "response_text": response,
            "provider": provider, "category": category,
            "created_at": now, "expires_at": now + ttl,
        }
        point_id = str(uuid.uuid4())

        try:
            await self._qdrant.upsert(
                collection_name=self._collection,
                points=[PointStruct(id=point_id, vector=vector, payload=payload)],
            )
        except Exception as e:
            logger.error("Qdrant upsert failed: {}", e)

        def lancedb_store() -> None:
            table = self._lancedb.open_table(self._collection)
            table.add([{"vector": vector, "id": point_id, **payload}])

        try:
            await asyncio.to_thread(lancedb_store)
        except Exception as e:
            logger.error("LanceDB store failed: {}", e)

    async def cleanup_expired(self) -> int:
        """Delete expired entries from both DBs."""
        now = time.time()
        deleted_count = 0

        try:
            f = Filter(must=[FieldCondition(key="expires_at", range=Range(lt=now))])
            count_res = await self._qdrant.count(self._collection, count_filter=f)
            deleted_count = int(count_res.count)
            await self._qdrant.delete(self._collection, points_selector=f)
        except Exception as e:
            logger.error("Qdrant cleanup failed: {}", e)

        def lancedb_clean() -> None:
            table = self._lancedb.open_table(self._collection)
            table.delete(f"expires_at < {now}")

        try:
            await asyncio.to_thread(lancedb_clean)
        except Exception as e:
            logger.error("LanceDB cleanup failed: {}", e)

        return deleted_count

    async def invalidate_provider(self, provider: str) -> None:
        """Invalidate all semantic cache entries for a provider."""
        try:
            f = Filter(must=[FieldCondition(key="provider", match=MatchValue(value=provider))])
            await self._qdrant.delete(self._collection, points_selector=f)
        except Exception as e:
            logger.error("Qdrant invalidation failed: {}", e)

        def lancedb_inval() -> None:
            table = self._lancedb.open_table(self._collection)
            table.delete(f"provider = '{provider}'")

        try:
            await asyncio.to_thread(lancedb_inval)
        except Exception as e:
            logger.error("LanceDB invalidation failed: {}", e)

