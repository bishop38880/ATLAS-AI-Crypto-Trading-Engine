"""RAG Write Pipeline — embeds and persists signal context.

Implements the write side of the RAG feedback loop:
    1. ``write_signal_context`` — embeds a SignalOutput summary into
       ``signal_history``.
    2. ``write_outcome`` — enriches an existing signal row with post-trade
       PnL data from PROMETHEUS.
    3. ``write_pattern`` — inserts curated lessons / post-mortems into
       ``pattern_memory``.

All embedding columns use pgvector type sized to ``EmbeddingService.vector_dimension``
(Must match ``PolarisSettings.embed_dimensions`` and Qdrant/LanceDB.)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from decimal import Decimal

import asyncpg  # type: ignore[import-untyped]
import msgspec
from loguru import logger

from atlas.models.signal import SignalOutput
from atlas.rag.embedding_service import EmbeddingService


# ---------------------------------------------------------------------------
# Prepared Statement Constants
# ---------------------------------------------------------------------------

INSERT_SIGNAL_QUERY = """
    INSERT INTO signal_history
        (signal_id, asset, timeframe, decision, score,
         confidence, raw_score, reasoning, embedding, metadata)
    VALUES
        ($1, $2, $3, $4, $5, $6, $7, $8, $9::vector, $10::jsonb)
    ON CONFLICT (signal_id) DO NOTHING
    RETURNING id
"""

UPDATE_OUTCOME_QUERY = """
    UPDATE signal_history
    SET pnl_pct       = $1,
        outcome_label = $2,
        outcome_at    = $3,
        exit_reason   = $4
    WHERE signal_id   = $5
      AND pnl_pct IS NULL
"""

INSERT_PATTERN_QUERY = """
    INSERT INTO pattern_memory
        (title, content, category, source,
         relevance_score, embedding, metadata)
    VALUES
        ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
    RETURNING id
"""


async def init_connection(conn: asyncpg.Connection) -> None:
    """Init callback for asyncpg pool to warm prepared statements."""
    await conn.prepare(INSERT_SIGNAL_QUERY)
    await conn.prepare(UPDATE_OUTCOME_QUERY)
    await conn.prepare(INSERT_PATTERN_QUERY)


# ---------------------------------------------------------------------------
# Text summary builder (pure function, no I/O)
# ---------------------------------------------------------------------------


def _build_signal_summary(signal: SignalOutput) -> str:
    """Build a text summary of a SignalOutput for embedding.

    The summary is designed to capture the semantic essence of the
    signal so that cosine-similarity retrieval surfaces the most
    relevant historical signals.

    Args:
        signal: The canonical signal output.

    Returns:
        A plain-text summary string.
    """
    parts: list[str] = [
        f"Asset: {signal.asset}",
        f"Decision: {signal.decision.value}",
        f"Score: {signal.score}/100 (raw {signal.raw_confluence_score}/220)",
        f"Confidence: {signal.confidence:.2f}",
        f"Timeframe: {signal.timeframe}",
    ]
    if signal.reasoning_summary:
        parts.append(f"Reasoning: {signal.reasoning_summary}")
    if signal.key_convergences:
        parts.append(
            "Convergences: " + "; ".join(signal.key_convergences)
        )
    if signal.key_risks:
        parts.append("Risks: " + "; ".join(signal.key_risks))
    if signal.deepseek_evaluation:
        parts.append(
            f"DeepSeek: {signal.deepseek_evaluation.reasoning}"
        )
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# RAGWriter
# ---------------------------------------------------------------------------


class RAGWriter:
    """Writes signal context and pattern memory to PostgreSQL.

    Dependencies are injected at construction time — no hidden
    global state.

    Args:
        pool: asyncpg connection pool.
        embedding_service: Embedding service (dimension from settings).
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_service: EmbeddingService,
    ) -> None:
        self._pool = pool
        self._embed = embedding_service
        self._embed_dim = embedding_service.vector_dimension

    # ── write_signal_context ──────────────────────────────────────────

    async def write_signal_context(
        self,
        signal: SignalOutput,
    ) -> str:
        """Embed and persist a SignalOutput to ``signal_history``.

        Args:
            signal: The canonical signal output.

        Returns:
            The UUID primary-key of the inserted row.
        """
        summary = _build_signal_summary(signal)
        embedding = await self._embed.embed(summary)
        row_id = await self._insert_signal_row(signal, embedding)
        logger.info(
            "Wrote signal context | signal_id={} row_id={}",
            signal.signal_id,
            row_id,
        )
        return str(row_id)

    async def _insert_signal_row(
        self,
        signal: SignalOutput,
        embedding: list[float],
    ) -> Any:
        """Insert a single row into ``signal_history``.

        Args:
            signal: The canonical signal output.
            embedding: Float vector (length ``self._embed_dim``).

        Returns:
            The UUID of the inserted row.
        """
        pgvector_literal = _to_pgvector_literal(embedding, self._embed_dim)
        metadata_json = msgspec.json.encode(
            {"category_scores": signal.category_scores.model_dump(mode="json")},
        ).decode()
        return await self._pool.fetchval(
            INSERT_SIGNAL_QUERY,
            signal.signal_id,
            signal.asset,
            signal.timeframe,
            signal.decision.value,
            signal.score,
            signal.confidence,
            signal.raw_confluence_score,
            signal.reasoning_summary,
            pgvector_literal,
            metadata_json,
            timeout=5.0,
        )

    # ── write_outcome ─────────────────────────────────────────────────

    async def write_outcome(
        self,
        signal_id: str,
        pnl_pct: Decimal,
        outcome_label: str,
        exit_reason: str,
    ) -> bool:
        """Enrich an existing signal_history row with post-trade PnL.

        Args:
            signal_id: The signal_id to update.
            pnl_pct: Post-trade PnL percentage.
            outcome_label: One of 'WIN', 'LOSS', 'SCRATCH'.
            exit_reason: Reason the trade was closed.

        Returns:
            True if a row was updated, False if signal_id not found.
        """
        _validate_outcome_label(outcome_label)
        updated = await self._execute_outcome_update(
            signal_id, pnl_pct, outcome_label, exit_reason,
        )
        if updated:
            logger.info(
                "Wrote outcome | signal_id={} pnl={}% label={} reason={}",
                signal_id, pnl_pct, outcome_label, exit_reason,
            )
        else:
            logger.warning(
                "Outcome write skipped | signal_id={} (not found or already set)",
                signal_id,
            )
        return updated

    async def _execute_outcome_update(
        self,
        signal_id: str,
        pnl_pct: Decimal,
        outcome_label: str,
        exit_reason: str,
    ) -> bool:
        """Execute the outcome UPDATE statement.

        Args:
            signal_id: The signal_id to update.
            pnl_pct: Post-trade PnL percentage.
            outcome_label: Validated outcome label.
            exit_reason: Reason the trade was closed.

        Returns:
            True if exactly one row was updated.
        """
        result = await self._pool.execute(
            UPDATE_OUTCOME_QUERY,
            pnl_pct,
            outcome_label,
            datetime.now(timezone.utc),
            exit_reason,
            signal_id,
            timeout=5.0,
        )
        return bool(result == "UPDATE 1")

    # ── write_pattern ─────────────────────────────────────────────────

    async def write_pattern(
        self,
        title: str,
        content: str,
        category: str = "general",
        source: str = "system",
        relevance_score: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Insert a curated lesson into ``pattern_memory``.

        Args:
            title: Short pattern title.
            content: Full lesson / post-mortem text.
            category: Classification tag (default 'general').
            source: Origin of the pattern (default 'system').
            relevance_score: Priority weight 0.0–1.0 (default 1.0).
            metadata: Optional JSON metadata (e.g. user_tags, flagged_asset).

        Returns:
            The UUID primary-key of the inserted row.
        """
        embedding = await self._embed.embed(f"{title}: {content}")
        meta = metadata if metadata is not None else {}
        row_id = await self._insert_pattern_row(
            title, content, category, source,
            relevance_score, embedding, meta,
        )
        logger.info(
            "Wrote pattern | title='{}' category={} row_id={}",
            title,
            category,
            row_id,
        )
        return str(row_id)

    async def _insert_pattern_row(
        self,
        title: str,
        content: str,
        category: str,
        source: str,
        relevance_score: float,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> Any:
        """Insert a single row into ``pattern_memory``.

        Args:
            title: Short pattern title.
            content: Full lesson text.
            category: Classification tag.
            source: Origin identifier.
            relevance_score: Priority weight.
            embedding: Float vector (length ``self._embed_dim``).
            metadata: JSON sidecar for UI / provenance.

        Returns:
            The UUID of the inserted row.
        """
        pgvector_literal = _to_pgvector_literal(embedding, self._embed_dim)
        meta_literal = msgspec.json.encode(metadata).decode()
        return await self._pool.fetchval(
            INSERT_PATTERN_QUERY,
            title,
            content,
            category,
            source,
            relevance_score,
            pgvector_literal,
            meta_literal,
            timeout=5.0,
        )

    async def tag_signal_metadata(
        self,
        signal_id: str,
        tags: list[str],
        note: str | None = None,
    ) -> bool:
        """Merge ``user_tags`` and optional ``user_note`` into ``signal_history.metadata``.

        Returns:
            True when a row was updated.
        """
        row = await self._pool.fetchrow(
            "SELECT metadata FROM signal_history WHERE signal_id = $1",
            signal_id,
            timeout=5.0,
        )
        if row is None:
            logger.warning("tag_signal_metadata_missing | signal_id={}", signal_id)
            return False
        raw_meta = row["metadata"]
        if isinstance(raw_meta, dict):
            meta = dict(raw_meta)
        elif raw_meta in (None, ""):
            meta = {}
        else:
            meta = {}

        prior_tags = meta.get("user_tags", [])
        if not isinstance(prior_tags, list):
            prior_tags = []
        merged = sorted(
            {str(t).strip() for t in prior_tags + tags if str(t).strip()},
        )
        meta["user_tags"] = merged
        if note is not None and note.strip():
            meta["user_note"] = note.strip()

        encoded = msgspec.json.encode(meta).decode()
        status = await self._pool.execute(
            "UPDATE signal_history SET metadata = $1::jsonb WHERE signal_id = $2",
            encoded,
            signal_id,
            timeout=5.0,
        )
        updated = status == "UPDATE 1"
        if updated:
            logger.info(
                "tagged_signal_memory | signal_id={} | tags={}",
                signal_id,
                merged,
            )
        return updated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_pgvector_literal(embedding: list[float], expected_dim: int) -> str:
    """Convert a float list to pgvector's text literal format.

    Args:
        embedding: Float vector (length ``expected_dim``).
        expected_dim: Must match PostgreSQL ``vector(n)`` and the embedding model.

    Returns:
        String like '[0.1,0.2,…]' accepted by pgvector's ``::vector`` cast.

    Raises:
        ValueError: If embedding length does not match ``expected_dim``.
    """
    if len(embedding) != expected_dim:
        raise ValueError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {len(embedding)}"
        )
    return "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"


def _validate_outcome_label(label: str) -> None:
    """Validate outcome_label is one of the permitted values.

    Args:
        label: The label to validate.

    Raises:
        ValueError: If the label is not permitted.
    """
    permitted = {"WIN", "LOSS", "SCRATCH"}
    if label not in permitted:
        raise ValueError(
            f"outcome_label must be one of {permitted}, got '{label}'"
        )
