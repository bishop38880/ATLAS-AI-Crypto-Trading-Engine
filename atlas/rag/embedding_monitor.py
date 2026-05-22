"""Embedding Drift Monitor — Phase 8 Real-Time Detection.

Detects when the embedding model (Mistral or local Qwen) drifts,
causing RAG retrieval quality degradation.  Compares new embeddings
against a fixed reference corpus and blocks model updates when
average cosine similarity drops below the 0.95 threshold.

Invariants:
    - Reference corpus is fixed and versioned (1000 entries).
    - Similarity threshold: 0.95 cosine similarity.
    - Block embedding model updates if drift is detected.
    - CPU-bound numpy offloaded via ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import asyncpg  # type: ignore[import-untyped]

import numpy as np
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel

from atlas.rag.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DRIFT_THRESHOLD: float = 0.95
DAILY_SAMPLE_SIZE: int = 100
ALERT_THROTTLE_TTL: int = 86400  # 24 hours

# ---------------------------------------------------------------------------
# SQL Constants
# ---------------------------------------------------------------------------

FETCH_REFERENCE_TEXTS_QUERY = """
    SELECT source_signal_id, content_text, embedding
    FROM embedding_reference
    ORDER BY created_at
"""

RANDOM_SUBSET_QUERY = """
    SELECT source_signal_id, content_text, embedding
    FROM embedding_reference
    ORDER BY RANDOM()
    LIMIT $1
"""


# ---------------------------------------------------------------------------
# Result Models (frozen)
# ---------------------------------------------------------------------------


class DriftCheckResult(BaseModel, frozen=True):
    """Result of an embedding model drift check.

    Attributes:
        avg_cosine_similarity: Mean cosine similarity across corpus.
        min_cosine_similarity: Worst-case similarity.
        max_cosine_similarity: Best-case similarity.
        corpus_size: Number of reference entries checked.
        drift_detected: True if avg < DRIFT_THRESHOLD.
        model_version: Identifier of the tested model.
        checked_at: UTC timestamp of this check.
    """

    avg_cosine_similarity: float
    min_cosine_similarity: float
    max_cosine_similarity: float
    corpus_size: int
    drift_detected: bool
    model_version: str
    checked_at: datetime


class DailyCorrelationResult(BaseModel, frozen=True):
    """Result of a daily API embedding correlation check.

    Attributes:
        avg_cosine_similarity: Mean cosine similarity of sample.
        sample_size: Number of reference texts re-embedded.
        drift_detected: True if avg < DRIFT_THRESHOLD.
        fallback_triggered: True if fallback to local model activated.
        checked_at: UTC timestamp of this check.
    """

    avg_cosine_similarity: float
    sample_size: int
    drift_detected: bool
    fallback_triggered: bool
    checked_at: datetime


# ---------------------------------------------------------------------------
# Cosine Similarity (pure numpy, offloaded to thread)
# ---------------------------------------------------------------------------


def _compute_cosine_similarities_sync(
    ref_embeddings: list[list[float]],
    new_embeddings: list[list[float]],
) -> dict[str, float]:
    """Compute per-pair cosine similarities between two embedding sets.

    Args:
        ref_embeddings: Original reference embeddings.
        new_embeddings: Newly computed embeddings.

    Returns:
        Dict with 'avg', 'min', 'max' cosine similarity.
    """
    ref = np.array(ref_embeddings, dtype=np.float64)
    new = np.array(new_embeddings, dtype=np.float64)

    ref_norms = np.linalg.norm(ref, axis=1, keepdims=True)
    new_norms = np.linalg.norm(new, axis=1, keepdims=True)

    ref_normed = np.where(ref_norms > 0, ref / ref_norms, ref)
    new_normed = np.where(new_norms > 0, new / new_norms, new)

    cosines = np.sum(ref_normed * new_normed, axis=1)

    return {
        "avg": float(np.mean(cosines)),
        "min": float(np.min(cosines)),
        "max": float(np.max(cosines)),
    }


# ---------------------------------------------------------------------------
# EmbeddingDriftMonitor
# ---------------------------------------------------------------------------


class EmbeddingDriftMonitor:
    """Monitors embedding model drift against a fixed reference corpus.

    Dependencies are injected at construction time.

    Args:
        pool: asyncpg connection pool.
        embedding_service: Current embedding service instance.
        redis_client: Async Redis client for fallback state + throttle.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_service: EmbeddingService,
        redis_client: redis_async.Redis,
    ) -> None:
        self._pool = pool
        self._embed = embedding_service
        self._redis = redis_client

    # ── Model Change Drift Check ─────────────────────────────────────

    async def check_model_drift(
        self,
        new_embedding_service: EmbeddingService,
        model_version: str = "unknown",
    ) -> DriftCheckResult:
        """Check if a new embedding model drifts from the reference.

        Re-embeds the full reference corpus with the candidate model
        and compares cosine similarity to stored embeddings.

        Args:
            new_embedding_service: Candidate embedding service.
            model_version: Identifier for the candidate model.

        Returns:
            DriftCheckResult with drift assessment.
        """
        ref_data = await self._fetch_reference_corpus()
        if not ref_data:
            return self._empty_drift_result(model_version)

        ref_texts = [r["content_text"] for r in ref_data]
        ref_embeddings = self._parse_embeddings(ref_data)

        new_embeddings = await new_embedding_service.embed_batch(ref_texts)

        stats = await asyncio.to_thread(
            _compute_cosine_similarities_sync,
            ref_embeddings,
            new_embeddings,
        )

        result = self._build_drift_result(stats, len(ref_data), model_version)

        if result.drift_detected:
            await self._handle_drift_detected(result)

        return result

    async def _fetch_reference_corpus(self) -> list[Any]:
        """Fetch all reference entries for drift comparison.

        Returns:
            List of Records with content_text and embedding.
        """
        return await self._pool.fetch(
            FETCH_REFERENCE_TEXTS_QUERY, timeout=30.0,
        )

    def _parse_embeddings(
        self,
        records: list[Any],
    ) -> list[list[float]]:
        """Extract embedding vectors from database records.

        Args:
            records: asyncpg Records with embedding column.

        Returns:
            List of float lists (1024-dim each).
        """
        result: list[list[float]] = []
        for r in records:
            emb = r["embedding"]
            if isinstance(emb, str):
                emb = _parse_pgvector_string(emb)
            result.append(list(emb))
        return result

    def _build_drift_result(
        self,
        stats: dict[str, float],
        corpus_size: int,
        model_version: str,
    ) -> DriftCheckResult:
        """Construct a DriftCheckResult from similarity stats.

        Args:
            stats: Dict with avg/min/max cosine values.
            corpus_size: Number of reference entries.
            model_version: Tested model identifier.

        Returns:
            Frozen DriftCheckResult.
        """
        return DriftCheckResult(
            avg_cosine_similarity=stats["avg"],
            min_cosine_similarity=stats["min"],
            max_cosine_similarity=stats["max"],
            corpus_size=corpus_size,
            drift_detected=stats["avg"] < DRIFT_THRESHOLD,
            model_version=model_version,
            checked_at=datetime.now(timezone.utc),
        )

    def _empty_drift_result(self, model_version: str) -> DriftCheckResult:
        """Return a safe default when no reference corpus exists.

        Args:
            model_version: Tested model identifier.

        Returns:
            DriftCheckResult with no drift and zero corpus.
        """
        logger.warning("No reference corpus found — skipping drift check")
        return DriftCheckResult(
            avg_cosine_similarity=1.0,
            min_cosine_similarity=1.0,
            max_cosine_similarity=1.0,
            corpus_size=0,
            drift_detected=False,
            model_version=model_version,
            checked_at=datetime.now(timezone.utc),
        )

    async def _handle_drift_detected(self, result: DriftCheckResult) -> None:
        """Log, alert, and block on drift detection.

        Args:
            result: The drift check result with stats.
        """
        logger.error(
            "EMBEDDING_DRIFT | avg_sim={:.4f} | min_sim={:.4f} | model={} | BLOCKED",
            result.avg_cosine_similarity,
            result.min_cosine_similarity,
            result.model_version,
        )
        await self._send_drift_alert(result, "model_change")

    # ── Daily API Correlation Check ──────────────────────────────────

    async def daily_api_correlation_check(self) -> DailyCorrelationResult:
        """Re-embed a random subset via the API and compare.

        Selects 100 reference texts, re-embeds via the current
        EmbeddingService, and compares to cached embeddings.
        Triggers fallback if correlation drops below threshold.

        Returns:
            DailyCorrelationResult with correlation assessment.
        """
        subset = await self._select_random_subset(DAILY_SAMPLE_SIZE)
        if not subset:
            return self._empty_correlation_result()

        ref_texts = [r["content_text"] for r in subset]
        ref_embeddings = self._parse_embeddings(subset)

        new_embeddings = await self._embed.embed_batch(ref_texts)

        stats = await asyncio.to_thread(
            _compute_cosine_similarities_sync,
            ref_embeddings,
            new_embeddings,
        )

        drift_detected = stats["avg"] < DRIFT_THRESHOLD
        fallback_triggered = False

        if drift_detected:
            await self._trigger_fallback()
            fallback_triggered = True
            await self._send_correlation_alert(stats)

        return DailyCorrelationResult(
            avg_cosine_similarity=stats["avg"],
            sample_size=len(subset),
            drift_detected=drift_detected,
            fallback_triggered=fallback_triggered,
            checked_at=datetime.now(timezone.utc),
        )

    async def _select_random_subset(
        self,
        n: int = DAILY_SAMPLE_SIZE,
    ) -> list[Any]:
        """Select a random subset of reference entries.

        Args:
            n: Number of entries to sample.

        Returns:
            List of Records with content_text and embedding.
        """
        return await self._pool.fetch(
            RANDOM_SUBSET_QUERY, n, timeout=15.0,
        )

    def _empty_correlation_result(self) -> DailyCorrelationResult:
        """Return safe default when reference corpus is empty.

        Returns:
            DailyCorrelationResult with no drift.
        """
        logger.warning("No reference corpus for daily check — skipping")
        return DailyCorrelationResult(
            avg_cosine_similarity=1.0,
            sample_size=0,
            drift_detected=False,
            fallback_triggered=False,
            checked_at=datetime.now(timezone.utc),
        )

    # ── Fallback ─────────────────────────────────────────────────────

    async def _trigger_fallback(self) -> None:
        """Switch to local Qwen embedding model via Redis flag."""
        await self._redis.set("embedding:active_provider", "qwen_local")
        logger.error(
            "EMBEDDING_FALLBACK | switched to qwen_local due to API drift"
        )

    # ── Alerting (throttled) ─────────────────────────────────────────

    async def _send_drift_alert(
        self,
        result: DriftCheckResult,
        alert_type: str,
    ) -> None:
        """Send throttled Telegram alert for model drift.

        Args:
            result: The drift check result.
            alert_type: Alert category key for throttle.
        """
        throttle_key = f"embedding_drift_alert:{alert_type}"
        if await self._redis.exists(throttle_key):
            return
        await self._redis.setex(
            throttle_key, ALERT_THROTTLE_TTL, "1",
        )
        try:
            from scripts.telegram_alert import send_telegram_alert
            msg = (
                "🚨 *Embedding Drift Detected*\n"
                f"Type: `{alert_type}`\n"
                f"Avg Similarity: `{result.avg_cosine_similarity:.4f}`\n"
                f"Min Similarity: `{result.min_cosine_similarity:.4f}`\n"
                f"Corpus Size: `{result.corpus_size}`\n"
                f"Model: `{result.model_version}`\n"
                "⛔ Model update *BLOCKED*"
            )
            await send_telegram_alert(msg)
        except Exception as exc:
            logger.warning(
                "embedding_drift_telegram_failed | error={}", str(exc),
            )

    async def _send_correlation_alert(
        self,
        stats: dict[str, float],
    ) -> None:
        """Send throttled Telegram alert for daily correlation drift.

        Args:
            stats: Dict with avg/min/max cosine values.
        """
        throttle_key = "embedding_drift_alert:daily_correlation"
        if await self._redis.exists(throttle_key):
            return
        await self._redis.setex(
            throttle_key, ALERT_THROTTLE_TTL, "1",
        )
        try:
            from scripts.telegram_alert import send_telegram_alert
            msg = (
                "🚨 *API Embedding Correlation Drop*\n"
                f"Avg Similarity: `{stats['avg']:.4f}`\n"
                f"Min Similarity: `{stats['min']:.4f}`\n"
                "⚠️ Fallback to `qwen_local` activated"
            )
            await send_telegram_alert(msg)
        except Exception as exc:
            logger.warning(
                "correlation_drift_telegram_failed | error={}", str(exc),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_pgvector_string(s: str) -> list[float]:
    """Parse pgvector text representation to float list.

    Args:
        s: String like '[0.1,0.2,…]'.

    Returns:
        List of floats.
    """
    cleaned = s.strip("[]")
    return [float(x) for x in cleaned.split(",")]
