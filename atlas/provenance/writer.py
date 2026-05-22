"""Decision provenance writer — append-only PostgreSQL persistence.

Inserts one ``ProvenanceRecord`` per emitted signal.  Non-blocking:
the write is dispatched as a fire-and-forget ``asyncio.Task`` so
it never slows the signal emission hot path.

Table contract:
    - ``decision_provenance`` is INSERT-only.  No UPDATE, no DELETE.
    - The writer never reads from the table (reads are for post-mortems).

Architecture constraints:
    - ``asyncpg`` only — no SQLAlchemy.
    - ``msgspec`` for JSON serialisation.
    - ``Loguru`` positional format.
    - Functions ≤ 40 lines.
"""

from __future__ import annotations

import asyncio
import subprocess
from typing import Any

import asyncpg
import msgspec
from loguru import logger

from atlas.provenance.models import ProvenanceRecord


_GIT_SHA_CACHE: str | None = None


def _get_git_sha() -> str:
    """Best-effort git SHA retrieval.  Cached after first call.

    Returns:
        Git short SHA or 'unknown' on failure.
    """
    global _GIT_SHA_CACHE  # noqa: PLW0603
    if _GIT_SHA_CACHE is not None:
        return _GIT_SHA_CACHE
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        _GIT_SHA_CACHE = result.stdout.strip() or "unknown"
    except Exception:
        _GIT_SHA_CACHE = "unknown"
    return _GIT_SHA_CACHE


class ProvenanceWriter:
    """Append-only writer for decision provenance records.

    Attributes:
        _pool: asyncpg connection pool.
    """

    _INSERT_SQL: str = (
        "INSERT INTO decision_provenance ("
        "  signal_id, timestamp, schema_version, git_sha,"
        "  input_bundle_sha256, agent_verdicts,"
        "  raw_confluence_score, normalised_score, decision,"
        "  pipeline_confidence, confidence_tier,"
        "  signal_output_sha256, cycle_latency_ms"
        ") VALUES ("
        "  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13"
        ")"
    )

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Initialize with asyncpg connection pool.

        Args:
            pool: asyncpg connection pool.
        """
        self._pool = pool
        self._queue: asyncio.Queue[ProvenanceRecord] = asyncio.Queue(maxsize=1000)
        self._worker_task: asyncio.Task | None = None
        self._shutting_down = False

    def start_worker(self) -> None:
        """Start the background worker task."""
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self) -> None:
        while not self._shutting_down or not self._queue.empty():
            records: list[ProvenanceRecord] = []
            try:
                if self._shutting_down and self._queue.empty():
                    break
                records = await self._drain_queue()
                if not records:
                    continue
                batch = self._build_batch(records)
                async with self._pool.acquire() as conn:
                    await conn.executemany(self._INSERT_SQL, batch)
                logger.info("provenance_batch_written | count={}", len(batch))
                for _ in records:
                    self._queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("provenance_worker_error | exc={}", exc)
                for _ in records:
                    self._queue.task_done()

    async def _drain_queue(self) -> list[ProvenanceRecord]:
        """Wait for and drain up to 100 records from the queue."""
        records: list[ProvenanceRecord] = []
        try:
            records.append(await asyncio.wait_for(self._queue.get(), timeout=1.0))
        except asyncio.TimeoutError:
            return records
        while len(records) < 100:
            try:
                records.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return records

    @staticmethod
    def _build_batch(records: list[ProvenanceRecord]) -> list[tuple[Any, ...]]:
        """Serialize records into SQL parameter tuples."""
        batch: list[tuple[Any, ...]] = []
        for record in records:
            verdicts_json = msgspec.json.encode(
                [v.model_dump() for v in record.agent_verdicts],
            ).decode("utf-8")
            batch.append((
                record.signal_id, record.timestamp,
                record.schema_version, record.git_sha,
                record.input_bundle_sha256, verdicts_json,
                record.raw_confluence_score, record.normalised_score,
                record.decision, record.pipeline_confidence,
                record.confidence_tier, record.signal_output_sha256,
                record.cycle_latency_ms,
            ))
        return batch

    async def close(self) -> None:
        """Gracefully drain and stop worker."""
        self._shutting_down = True
        if self._worker_task:
            await self._queue.join()
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                raise

    async def write(self, record: ProvenanceRecord) -> None:
        """Insert a single provenance record (legacy sync interface for tests)."""
        verdicts_json = msgspec.json.encode(
            [v.model_dump() for v in record.agent_verdicts],
        ).decode("utf-8")

        async with self._pool.acquire() as conn:
            await conn.execute(
                self._INSERT_SQL,
                record.signal_id,
                record.timestamp,
                record.schema_version,
                record.git_sha,
                record.input_bundle_sha256,
                verdicts_json,
                record.raw_confluence_score,
                record.normalised_score,
                record.decision,
                record.pipeline_confidence,
                record.confidence_tier,
                record.signal_output_sha256,
                record.cycle_latency_ms,
            )
        logger.info(
            "provenance_written | signal_id={}", record.signal_id,
        )

    def write_async(self, record: ProvenanceRecord) -> None:
        """Queue provenance write — never blocks the hot path.

        Drops the record if the bounded queue is full.
        """
        if self._worker_task is None:
            self.start_worker()
            
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.error("provenance_queue_full | signal_id={}", record.signal_id)
