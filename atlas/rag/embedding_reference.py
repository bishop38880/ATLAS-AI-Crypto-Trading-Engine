"""Embedding Reference Corpus Builder — Phase 8 Drift Monitoring.

Builds and manages a fixed reference corpus of 1000 diverse trade
analyses from ``signal_history``, stratified by asset, outcome, and
score range.  Stored embeddings serve as the ground-truth baseline
for detecting embedding model drift.

All embedding columns are ``vector(1024)`` — Mistral compatible.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from loguru import logger

from atlas.rag.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# SQL Constants
# ---------------------------------------------------------------------------

STRATIFIED_CANDIDATES_QUERY = """
    WITH bucketed AS (
        SELECT
            signal_id,
            asset,
            outcome_label,
            score,
            reasoning AS content_text,
            CASE
                WHEN raw_score <= 55  THEN 'low'
                WHEN raw_score <= 110 THEN 'mid_low'
                WHEN raw_score <= 165 THEN 'mid_high'
                ELSE 'high'
            END AS score_bucket,
            ROW_NUMBER() OVER (
                PARTITION BY asset,
                             outcome_label,
                             CASE
                                 WHEN raw_score <= 55  THEN 'low'
                                 WHEN raw_score <= 110 THEN 'mid_low'
                                 WHEN raw_score <= 165 THEN 'mid_high'
                                 ELSE 'high'
                             END
                ORDER BY RANDOM()
            ) AS rn
        FROM signal_history
        WHERE reasoning IS NOT NULL
          AND reasoning <> ''
    )
    SELECT signal_id, asset, outcome_label, score, content_text
    FROM bucketed
    WHERE rn <= $1
    ORDER BY RANDOM()
    LIMIT $2
"""

INSERT_REFERENCE_QUERY = """
    INSERT INTO embedding_reference
        (source_signal_id, asset, outcome_label, score,
         content_hash, content_text, embedding, model_version)
    VALUES
        ($1, $2, $3, $4, $5, $6, $7::vector, $8)
    ON CONFLICT (content_hash) DO NOTHING
"""

FETCH_CORPUS_QUERY = """
    SELECT source_signal_id, asset, content_text, embedding
    FROM embedding_reference
    ORDER BY created_at
"""

CORPUS_VERSION_QUERY = """
    SELECT MAX(created_at) FROM embedding_reference
"""

CORPUS_COUNT_QUERY = """
    SELECT COUNT(*) FROM embedding_reference
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def compute_content_hash(text: str) -> str:
    """Compute SHA-256 hash of content text for deduplication.

    Args:
        text: The content string to hash.

    Returns:
        Hex-encoded SHA-256 digest.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _to_pgvector_literal(embedding: list[float]) -> str:
    """Convert a float list to pgvector text literal format.

    Args:
        embedding: 1024-dim float vector.

    Returns:
        String like '[0.1,0.2,…]' for pgvector cast.

    Raises:
        ValueError: If embedding is not 1024 dimensions.
    """
    if len(embedding) != 1024:
        raise ValueError(
            f"Embedding dimension mismatch: expected 1024, got {len(embedding)}"
        )
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


# ---------------------------------------------------------------------------
# ReferenceCorpusBuilder
# ---------------------------------------------------------------------------


class ReferenceCorpusBuilder:
    """Builds and manages the fixed embedding reference corpus.

    Selects diverse trade analyses from ``signal_history`` using
    stratified sampling, embeds them, and stores in
    ``embedding_reference`` for drift comparison.

    Args:
        pool: asyncpg connection pool.
        embedding_service: Mistral embedding service (1024-dim).
        model_version: Identifier for current embedding model.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_service: EmbeddingService,
        model_version: str = "mistral-embed",
    ) -> None:
        self._pool = pool
        self._embed = embedding_service
        self._model_version = model_version

    async def build_reference_corpus(
        self,
        target_size: int = 1000,
        rows_per_stratum: int = 10,
    ) -> int:
        """Build the reference corpus from signal_history.

        Args:
            target_size: Total number of reference entries.
            rows_per_stratum: Max rows sampled per stratum bucket.

        Returns:
            Number of entries inserted.
        """
        candidates = await self._fetch_stratified_candidates(
            rows_per_stratum, target_size,
        )
        if not candidates:
            logger.warning("No candidates found for reference corpus")
            return 0

        inserted = await self._embed_and_store_batch(candidates)
        logger.info(
            "Reference corpus built | target={} candidates={} inserted={}",
            target_size, len(candidates), inserted,
        )
        return inserted

    async def _fetch_stratified_candidates(
        self,
        rows_per_stratum: int,
        target_size: int,
    ) -> list[asyncpg.Record]:
        """Fetch stratified candidates from signal_history.

        Args:
            rows_per_stratum: Max rows per partition bucket.
            target_size: Overall limit on returned rows.

        Returns:
            List of asyncpg Records with signal data.
        """
        return await self._pool.fetch(
            STRATIFIED_CANDIDATES_QUERY,
            rows_per_stratum,
            target_size,
            timeout=30.0,
        )

    async def _embed_and_store_batch(
        self,
        candidates: list[asyncpg.Record],
    ) -> int:
        """Embed candidates and insert into embedding_reference.

        Processes in batches of 32 to avoid API rate limits.

        Args:
            candidates: Records from stratified query.

        Returns:
            Number of rows successfully inserted.
        """
        batch_size = 32
        inserted = 0

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]
            texts = [r["content_text"] for r in batch]
            embeddings = await self._embed.embed_batch(texts)
            inserted += await self._store_batch(batch, embeddings)

        return inserted

    async def _store_batch(
        self,
        batch: list[asyncpg.Record],
        embeddings: list[list[float]],
    ) -> int:
        """Store a batch of candidates with their embeddings.

        Args:
            batch: Records from stratified query.
            embeddings: Corresponding 1024-dim vectors.

        Returns:
            Number of rows inserted (excludes conflicts).
        """
        inserted = 0
        async with self._pool.acquire() as conn:
            for record, emb in zip(batch, embeddings):
                result = await self._insert_single(conn, record, emb)
                if result:
                    inserted += 1
        return inserted

    async def _insert_single(
        self,
        conn: asyncpg.Connection,
        record: asyncpg.Record,
        embedding: list[float],
    ) -> bool:
        """Insert a single reference entry.

        Args:
            conn: Active asyncpg connection.
            record: Source signal data.
            embedding: 1024-dim float vector.

        Returns:
            True if inserted, False if duplicate hash.
        """
        content_hash = compute_content_hash(record["content_text"])
        pgvec = _to_pgvector_literal(embedding)
        try:
            await conn.execute(
                INSERT_REFERENCE_QUERY,
                record["signal_id"],
                record["asset"],
                record["outcome_label"],
                record["score"],
                content_hash,
                record["content_text"],
                pgvec,
                self._model_version,
                timeout=5.0,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_reference_corpus(
        self,
    ) -> list[asyncpg.Record]:
        """Fetch the full reference corpus for drift comparison.

        Returns:
            List of Records with content_text and embedding.
        """
        return await self._pool.fetch(
            FETCH_CORPUS_QUERY, timeout=30.0,
        )

    async def get_corpus_version(self) -> datetime | None:
        """Get the latest creation timestamp as corpus version.

        Returns:
            UTC datetime of the newest entry, or None if empty.
        """
        result = await self._pool.fetchval(
            CORPUS_VERSION_QUERY, timeout=5.0,
        )
        return result

    async def get_corpus_size(self) -> int:
        """Get the current number of reference entries.

        Returns:
            Count of rows in embedding_reference.
        """
        result = await self._pool.fetchval(
            CORPUS_COUNT_QUERY, timeout=5.0,
        )
        return int(result or 0)
