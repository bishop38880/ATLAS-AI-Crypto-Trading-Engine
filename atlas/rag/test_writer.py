"""Tests for RAG write pipeline and migration runner.

Co-located tests validate:
    1. Text summary builder produces sensible strings.
    2. Embedding dimension matches ``EmbeddingService.vector_dimension``.
    3. RAGWriter methods call the correct SQL with correct params.
    4. Outcome label validation rejects bad values.
    5. Migration runner idempotency logic.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent
from atlas.rag.writer import (
    RAGWriter,
    _build_signal_summary,
    _to_pgvector_literal,
    _validate_outcome_label,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_signal(**overrides: object) -> SignalOutput:
    """Build a minimal valid SignalOutput for testing."""
    now = datetime.now(timezone.utc)
    defaults: dict = {
        "signal_id": str(uuid.uuid4()),
        "timestamp": now,
        "decision": SignalDecision.BUY,
        "asset": "BTCUSDT",
        "timeframe": "30m",
        "action": ActionBlock(
            side="buy",
            order_type="limit",
            price=Decimal("67000"),
            stop_loss=Decimal("66000"),
            take_profit=Decimal("69000"),
        ),
        "expires_at": now + timedelta(minutes=30),
        "reasoning_summary": "Strong momentum with volume confirmation.",
        "key_convergences": ["MACD bullish cross", "Whale accumulation"],
        "key_risks": ["High funding rate"],
        "score": 72,
        "confidence": 0.85,
        "category_scores": CategoryScores(
            technical=20, derivatives=15, sentiment=10,
            whale=8, onchain=5, liquidation=4,
            regime=5, funding=3, news_macro=2,
            correlation=0, context=0, total=72,
        ),
        "raw_confluence_score": 158,
        "telemetry": TelemetryEvent(cycle_id="test-001", cycle_latency_ms=42.0, agent_count=10),
    }
    defaults.update(overrides)
    return SignalOutput(**defaults)


def _fake_embedding(dim: int = 1024) -> list[float]:
    """Return a deterministic fake embedding."""
    return [0.01 * (i % 100) for i in range(dim)]


@pytest.fixture()
def mock_pool() -> AsyncMock:
    """Async mock for asyncpg.Pool."""
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=uuid.uuid4())
    pool.execute = AsyncMock(return_value="UPDATE 1")
    return pool


@pytest.fixture()
def mock_embed_service() -> AsyncMock:
    """Async mock for EmbeddingService."""
    svc = AsyncMock()
    svc.vector_dimension = 1024
    svc.embed = AsyncMock(return_value=_fake_embedding(1024))
    return svc


@pytest.fixture()
def writer(
    mock_pool: AsyncMock,
    mock_embed_service: AsyncMock,
) -> RAGWriter:
    """RAGWriter with mocked dependencies."""
    return RAGWriter(pool=mock_pool, embedding_service=mock_embed_service)


# ---------------------------------------------------------------------------
# _build_signal_summary
# ---------------------------------------------------------------------------


class TestBuildSignalSummary:
    """Validate text summary construction."""

    def test_basic_fields_present(self) -> None:
        signal = _make_signal()
        summary = _build_signal_summary(signal)
        assert "BTCUSDT" in summary
        assert "Buy" in summary
        assert "72/100" in summary
        assert "158/220" in summary
        assert "0.85" in summary

    def test_reasoning_included(self) -> None:
        signal = _make_signal(reasoning_summary="Test reasoning.")
        summary = _build_signal_summary(signal)
        assert "Test reasoning." in summary

    def test_convergences_included(self) -> None:
        signal = _make_signal(key_convergences=["Alpha", "Beta"])
        summary = _build_signal_summary(signal)
        assert "Alpha" in summary
        assert "Beta" in summary

    def test_risks_included(self) -> None:
        signal = _make_signal(key_risks=["Risk1"])
        summary = _build_signal_summary(signal)
        assert "Risk1" in summary

    def test_empty_optional_fields(self) -> None:
        signal = _make_signal(
            decision=SignalDecision.HOLD,
            action=None,
            reasoning_summary="",
            key_convergences=[],
            key_risks=[],
        )
        summary = _build_signal_summary(signal)
        assert "Hold" in summary
        assert "Reasoning" not in summary


# ---------------------------------------------------------------------------
# _to_pgvector_literal
# ---------------------------------------------------------------------------


class TestPgvectorLiteral:
    """Validate pgvector serialization."""

    def test_correct_format(self) -> None:
        emb = [0.1, 0.2, 0.3] * 341 + [0.4]
        assert len(emb) == 1024
        result = _to_pgvector_literal(emb, 1024)
        assert result.startswith("[")
        assert result.endswith("]")
        assert result.count(",") == 1023

    def test_wrong_dimension_raises(self) -> None:
        with pytest.raises(ValueError, match="expected 1024, got 512"):
            _to_pgvector_literal([0.0] * 512, 1024)

    def test_wrong_dimension_768_raises(self) -> None:
        with pytest.raises(ValueError, match="expected 1024, got 768"):
            _to_pgvector_literal([0.0] * 768, 1024)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="expected 1024, got 0"):
            _to_pgvector_literal([], 1024)


# ---------------------------------------------------------------------------
# _validate_outcome_label
# ---------------------------------------------------------------------------


class TestValidateOutcomeLabel:
    """Validate outcome label enforcement."""

    @pytest.mark.parametrize("label", ["WIN", "LOSS", "SCRATCH"])
    def test_valid_labels(self, label: str) -> None:
        _validate_outcome_label(label)  # should not raise

    @pytest.mark.parametrize("label", ["win", "Draw", "INVALID", ""])
    def test_invalid_labels(self, label: str) -> None:
        with pytest.raises(ValueError, match="outcome_label"):
            _validate_outcome_label(label)


# ---------------------------------------------------------------------------
# RAGWriter.write_signal_context
# ---------------------------------------------------------------------------


class TestWriteSignalContext:
    """Validate signal context write flow."""

    @pytest.mark.asyncio()
    async def test_embeds_and_inserts(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
        mock_embed_service: AsyncMock,
    ) -> None:
        signal = _make_signal()
        row_id = await writer.write_signal_context(signal)

        mock_embed_service.embed.assert_awaited_once()
        mock_pool.fetchval.assert_awaited_once()
        assert row_id is not None

    @pytest.mark.asyncio()
    async def test_passes_correct_signal_id(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
    ) -> None:
        signal = _make_signal(signal_id="test-sig-001")
        await writer.write_signal_context(signal)

        call_args = mock_pool.fetchval.call_args
        assert call_args[0][1] == "test-sig-001"

    @pytest.mark.asyncio()
    async def test_passes_1024_embedding(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
    ) -> None:
        signal = _make_signal()
        await writer.write_signal_context(signal)

        call_args = mock_pool.fetchval.call_args
        pgvec_arg = call_args[0][9]  # 9th positional (embedding)
        assert pgvec_arg.startswith("[")
        assert pgvec_arg.count(",") == 1023
        meta_arg = call_args[0][10]
        assert '"category_scores"' in meta_arg
        assert '"derivatives"' in meta_arg


# ---------------------------------------------------------------------------
# RAGWriter.write_outcome
# ---------------------------------------------------------------------------


class TestWriteOutcome:
    """Validate outcome enrichment."""

    @pytest.mark.asyncio()
    async def test_successful_update(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
    ) -> None:
        result = await writer.write_outcome("sig-1", Decimal("2.5"), "WIN", "test")
        assert result is True
        mock_pool.execute.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_no_match_returns_false(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
    ) -> None:
        mock_pool.execute.return_value = "UPDATE 0"
        result = await writer.write_outcome("missing", Decimal("-1.0"), "LOSS", "test")
        assert result is False

    @pytest.mark.asyncio()
    async def test_invalid_label_raises(
        self,
        writer: RAGWriter,
    ) -> None:
        with pytest.raises(ValueError, match="outcome_label"):
            await writer.write_outcome("sig-1", Decimal("0.0"), "INVALID", "test")


# ---------------------------------------------------------------------------
# RAGWriter.write_pattern
# ---------------------------------------------------------------------------


class TestWritePattern:
    """Validate pattern memory writes."""

    @pytest.mark.asyncio()
    async def test_embeds_title_and_content(
        self,
        writer: RAGWriter,
        mock_embed_service: AsyncMock,
    ) -> None:
        await writer.write_pattern(
            title="Funding spike reversal",
            content="When funding exceeds 0.1%, price tends to reverse.",
        )
        call_text = mock_embed_service.embed.call_args[0][0]
        assert "Funding spike reversal" in call_text
        assert "price tends to reverse" in call_text

    @pytest.mark.asyncio()
    async def test_returns_uuid(
        self,
        writer: RAGWriter,
    ) -> None:
        row_id = await writer.write_pattern(
            title="Test", content="Content",
        )
        assert row_id is not None

    @pytest.mark.asyncio()
    async def test_custom_category_and_source(
        self,
        writer: RAGWriter,
        mock_pool: AsyncMock,
    ) -> None:
        await writer.write_pattern(
            title="T",
            content="C",
            category="post_mortem",
            source="human",
            relevance_score=0.8,
        )
        call_args = mock_pool.fetchval.call_args
        args_tuple = call_args[0]
        assert args_tuple[1] == "T"
        assert args_tuple[2] == "C"
        assert args_tuple[3] == "post_mortem"
        assert args_tuple[4] == "human"
        assert args_tuple[5] == 0.8
        meta_arg = args_tuple[7]
        assert isinstance(meta_arg, str)
        assert "{" in meta_arg


# ---------------------------------------------------------------------------
# Migration runner — idempotency
# ---------------------------------------------------------------------------


class TestMigrationRunner:
    """Validate migration runner logic (no real DB)."""

    @pytest.mark.asyncio()
    async def test_already_applied_returns_true(self) -> None:
        from atlas.rag.migrations.run_migrations import _already_applied

        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=1)
        assert await _already_applied(conn, "001_rag_tables.sql") is True

    @pytest.mark.asyncio()
    async def test_not_applied_returns_false(self) -> None:
        from atlas.rag.migrations.run_migrations import _already_applied

        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=None)
        assert await _already_applied(conn, "001_rag_tables.sql") is False

    @pytest.mark.asyncio()
    async def test_ensure_tracker_creates_table(self) -> None:
        from atlas.rag.migrations.run_migrations import _ensure_tracker

        conn = AsyncMock()
        conn.execute = AsyncMock()
        await _ensure_tracker(conn)
        conn.execute.assert_awaited_once()
        sql = conn.execute.call_args[0][0]
        assert "rag_migrations" in sql

    @pytest.mark.asyncio()
    async def test_apply_migration_inserts_record(self) -> None:
        from atlas.rag.migrations.run_migrations import _apply_migration

        conn = AsyncMock()
        conn.execute = AsyncMock()

        # asyncpg: conn.transaction() is sync, returns async CM
        tx_cm = MagicMock()
        tx_cm.__aenter__ = AsyncMock(return_value=None)
        tx_cm.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx_cm)

        await _apply_migration(conn, "001_test.sql", "SELECT 1;")
        # Two execute calls: the SQL itself + the INSERT
        assert conn.execute.await_count == 2
