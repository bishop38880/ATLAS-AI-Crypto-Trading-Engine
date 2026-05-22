"""Memory Archiver — bulk evaluates and soft-deletes stale RAG documents.

Orchestrates the nightly Agent Zero cycle:
    1. Fetch all active (non-archived) documents from PostgreSQL.
    2. Batch-fetch Redis retrieval and contradiction counters.
    3. Compute Escores for each document.
    4. Bulk UPDATE archived documents in PostgreSQL.
    5. Mark archived vectors in Qdrant metadata.
    6. Log the run summary to ``rag_archive_log``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncio

import asyncpg  # type: ignore[import-untyped]
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PointIdsList
from redis.asyncio import Redis

from atlas.rag.agent_zero.models import ArchiveSummary, EscoreRecord
from atlas.rag.agent_zero.scorer import EscoreCalculator
from atlas.rag.query import SIGNAL_MEMORY_COLLECTION
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# SQL Constants
# ---------------------------------------------------------------------------

_FETCH_ACTIVE_SQL = """
    SELECT signal_id, asset, created_at, outcome_label
    FROM signal_history
    WHERE (archived IS NULL OR archived = FALSE)
    ORDER BY created_at ASC
"""

_BULK_ARCHIVE_SQL = """
    UPDATE signal_history
    SET archived    = TRUE,
        archived_at = NOW(),
        escore      = $2
    WHERE signal_id = $1
      AND (archived IS NULL OR archived = FALSE)
"""

_INSERT_LOG_SQL = """
    INSERT INTO rag_archive_log
        (run_at, total_scored, archived_count, retained_count,
         avg_escore, lowest_kept_escore, highest_archived_escore, error_count)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
"""


# ---------------------------------------------------------------------------
# MemoryArchiver
# ---------------------------------------------------------------------------


class MemoryArchiver:
    """Evaluates and archives stale RAG memory documents.

    Args:
        pool: asyncpg connection pool.
        redis: redis.asyncio client.
        qdrant: Async Qdrant client.
        settings: PolarisSettings with agent_zero_threshold.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        redis: Redis,  # type: ignore[type-arg]
        qdrant: AsyncQdrantClient,
        settings: PolarisSettings,
    ) -> None:
        """Initialise with database clients and settings."""
        self._pool = pool
        self._redis = redis
        self._qdrant = qdrant
        self._settings = settings
        self._calculator = EscoreCalculator(
            threshold=settings.agent_zero_threshold,
        )
        self._collection = SIGNAL_MEMORY_COLLECTION

    async def run_nightly_cycle(self) -> ArchiveSummary:
        """Execute the full Agent Zero scoring and archival cycle.

        Returns:
            ArchiveSummary with statistics from the run.
        """
        run_at = datetime.now(timezone.utc)
        logger.info("Agent Zero starting nightly cycle")

        rows = await self._fetch_active_documents()
        if not rows:
            logger.info("Agent Zero: no active documents to evaluate")
            return self._empty_summary(run_at)

        doc_ids = [r["signal_id"] for r in rows]
        counters = await self._batch_fetch_redis_counters(doc_ids)
        records = self._score_all_documents(rows, counters)

        to_archive = [r for r in records if r.should_archive]
        to_retain = [r for r in records if not r.should_archive]

        error_count = await self._execute_archival(to_archive)
        summary = self._build_summary(
            run_at, records, to_archive, to_retain, error_count,
        )

        await self._log_summary(summary)
        logger.info(
            "Agent Zero complete | scored={} archived={} retained={} avg={}",
            summary.total_scored, summary.archived_count,
            summary.retained_count, summary.avg_escore,
        )
        return summary

    # ── Data fetching ─────────────────────────────────────────────────

    async def _fetch_active_documents(self) -> list[asyncpg.Record]:
        """Fetch all non-archived documents from signal_history.

        Returns:
            List of asyncpg Records with signal metadata.
        """
        return await self._pool.fetch(_FETCH_ACTIVE_SQL, timeout=30.0)

    async def _batch_fetch_redis_counters(
        self,
        doc_ids: list[str],
    ) -> dict[str, dict[str, int]]:
        """Batch-fetch retrieval and contradiction counters from Redis.

        Args:
            doc_ids: List of signal IDs to look up.

        Returns:
            Mapping of doc_id → {retrieval_count, contradiction_count}.
        """
        pipe = self._redis.pipeline(transaction=False)
        for doc_id in doc_ids:
            pipe.get(f"rag:retrieval_count:{doc_id}")
            pipe.get(f"rag:contradiction_count:{doc_id}")

        results: list[Any] = await asyncio.wait_for(
            pipe.execute(), timeout=15.0,
        )
        counters: dict[str, dict[str, int]] = {}

        for i, doc_id in enumerate(doc_ids):
            retrieval_raw = results[i * 2]
            contradiction_raw = results[i * 2 + 1]
            counters[doc_id] = {
                "retrieval_count": int(retrieval_raw) if retrieval_raw else 0,
                "contradiction_count": int(contradiction_raw) if contradiction_raw else 0,
            }
        return counters

    # ── Scoring ───────────────────────────────────────────────────────

    def _score_all_documents(
        self,
        rows: list[asyncpg.Record],
        counters: dict[str, dict[str, int]],
    ) -> list[EscoreRecord]:
        """Score every active document using the EscoreCalculator.

        Args:
            rows: Active document rows from PostgreSQL.
            counters: Redis counters keyed by signal_id.

        Returns:
            List of EscoreRecords.
        """
        records: list[EscoreRecord] = []
        for row in rows:
            sid = row["signal_id"]
            redis_data = counters.get(sid, {})
            metadata: dict[str, object] = {
                "signal_id": sid,
                "created_at": row["created_at"],
                "outcome_label": row["outcome_label"],
                "retrieval_count": redis_data.get("retrieval_count", 0),
                "contradiction_count": redis_data.get("contradiction_count", 0),
            }
            records.append(self._calculator.calculate(metadata))
        return records

    # ── Archival execution ────────────────────────────────────────────

    async def _execute_archival(
        self,
        to_archive: list[EscoreRecord],
    ) -> int:
        """Archive documents in PostgreSQL and Qdrant.

        Args:
            to_archive: EscoreRecords flagged for archival.

        Returns:
            Number of errors encountered.
        """
        if not to_archive:
            return 0

        error_count = 0
        error_count += await self._bulk_archive_postgres(to_archive)
        error_count += await self._bulk_archive_qdrant(to_archive)
        return error_count

    async def _bulk_archive_postgres(
        self,
        records: list[EscoreRecord],
    ) -> int:
        """Bulk-update signal_history to mark documents as archived.

        Args:
            records: EscoreRecords to archive.

        Returns:
            Number of errors (0 on success).
        """
        try:
            async with self._pool.acquire() as conn:
                await conn.executemany(
                    _BULK_ARCHIVE_SQL,
                    [(r.doc_id, r.final_escore) for r in records],
                    timeout=30.0,
                )
            return 0
        except Exception as exc:
            logger.error("Agent Zero PG archive failed | error={}", str(exc))
            return 1

    async def _bulk_archive_qdrant(
        self,
        records: list[EscoreRecord],
    ) -> int:
        """Mark archived vectors in Qdrant with metadata flag.

        Args:
            records: EscoreRecords to archive in Qdrant.

        Returns:
            Number of errors (0 on success).
        """
        try:
            point_ids = [r.doc_id for r in records]
            await asyncio.wait_for(
                self._qdrant.set_payload(
                    collection_name=self._collection,
                    payload={"archived": True},
                    points=PointIdsList(
                        points=point_ids,  # type: ignore[arg-type]
                    ),
                ),
                timeout=30.0,
            )
            return 0
        except Exception as exc:
            logger.error("Agent Zero Qdrant archive failed | error={}", str(exc))
            return 1

    # ── Summary & logging ─────────────────────────────────────────────

    def _build_summary(
        self,
        run_at: datetime,
        all_records: list[EscoreRecord],
        archived: list[EscoreRecord],
        retained: list[EscoreRecord],
        error_count: int,
    ) -> ArchiveSummary:
        """Build an ArchiveSummary from scored records.

        Args:
            run_at: Timestamp of the run.
            all_records: All scored documents.
            archived: Documents below threshold.
            retained: Documents at or above threshold.
            error_count: Number of errors during archival.

        Returns:
            Frozen ArchiveSummary model.
        """
        all_scores = [r.final_escore for r in all_records]
        avg = sum(all_scores) / len(all_scores) if all_scores else 0.0

        kept_scores = [r.final_escore for r in retained]
        archived_scores = [r.final_escore for r in archived]

        return ArchiveSummary(
            run_at=run_at,
            total_scored=len(all_records),
            archived_count=len(archived),
            retained_count=len(retained),
            avg_escore=round(avg, 4),
            lowest_kept_escore=min(kept_scores) if kept_scores else 0.0,
            highest_archived_escore=max(archived_scores) if archived_scores else 0.0,
            error_count=error_count,
        )

    def _empty_summary(self, run_at: datetime) -> ArchiveSummary:
        """Return an empty summary when no documents exist."""
        return ArchiveSummary(
            run_at=run_at,
            total_scored=0,
            archived_count=0,
            retained_count=0,
            avg_escore=0.0,
            lowest_kept_escore=0.0,
            highest_archived_escore=0.0,
        )

    async def _log_summary(self, summary: ArchiveSummary) -> None:
        """Persist the archive summary to rag_archive_log.

        Args:
            summary: The completed run summary.
        """
        try:
            await self._pool.execute(
                _INSERT_LOG_SQL,
                summary.run_at,
                summary.total_scored,
                summary.archived_count,
                summary.retained_count,
                summary.avg_escore,
                summary.lowest_kept_escore,
                summary.highest_archived_escore,
                summary.error_count,
                timeout=5.0,
            )
        except Exception as exc:
            logger.error("Agent Zero log insert failed | error={}", str(exc))
