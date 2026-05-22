"""Tests for decision provenance models and writer.

Tests live alongside code (atlas/provenance/test_provenance.py).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.provenance.models import (
    AgentVerdict,
    ProvenanceRecord,
    compute_sha256,
)
from atlas.provenance.writer import ProvenanceWriter, _get_git_sha


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestAgentVerdict:
    """AgentVerdict is a simple frozen snapshot."""

    def test_create_valid(self) -> None:
        """Valid verdict is accepted."""
        v = AgentVerdict(
            agent_name="technical",
            state="READY",
            score=50,
            max_score=75,
            direction="bullish",
        )
        assert v.agent_name == "technical"
        assert v.veto is False

    def test_negative_score_rejected(self) -> None:
        """Negative score raises ValidationError."""
        with pytest.raises(Exception):
            AgentVerdict(
                agent_name="x", state="READY",
                score=-1, max_score=10, direction="neutral",
            )


class TestProvenanceRecord:
    """ProvenanceRecord is the full audit trail for one signal."""

    def test_create_valid(self) -> None:
        """Valid record is accepted."""
        record = ProvenanceRecord(
            signal_id="sig-001",
            timestamp=datetime.now(timezone.utc),
            schema_version="2.0.0",
            git_sha="abc1234",
            input_bundle_sha256="aaa",
            agent_verdicts=[
                AgentVerdict(
                    agent_name="technical",
                    state="READY",
                    score=50,
                    max_score=75,
                    direction="bullish",
                ),
            ],
            raw_confluence_score=150,
            normalised_score=68,
            decision="BUY",
            pipeline_confidence=0.85,
            signal_output_sha256="bbb",
            cycle_latency_ms=120.0,
        )
        assert record.signal_id == "sig-001"
        assert len(record.agent_verdicts) == 1

    def test_raw_score_out_of_range_rejected(self) -> None:
        """Raw confluence score > 220 raises."""
        with pytest.raises(Exception):
            ProvenanceRecord(
                signal_id="sig-002",
                timestamp=datetime.now(timezone.utc),
                schema_version="2.0.0",
                input_bundle_sha256="x",
                agent_verdicts=[],
                raw_confluence_score=221,
                normalised_score=100,
                decision="BUY",
                signal_output_sha256="y",
            )

    def test_normalised_score_out_of_range_rejected(self) -> None:
        """Normalised score > 100 raises."""
        with pytest.raises(Exception):
            ProvenanceRecord(
                signal_id="sig-003",
                timestamp=datetime.now(timezone.utc),
                schema_version="2.0.0",
                input_bundle_sha256="x",
                agent_verdicts=[],
                raw_confluence_score=100,
                normalised_score=101,
                decision="BUY",
                signal_output_sha256="y",
            )


class TestComputeSha256:
    """SHA-256 utility."""

    def test_known_hash(self) -> None:
        """Known input produces expected hash prefix."""
        digest = compute_sha256(b"hello world")
        assert digest.startswith("b94d27b9")

    def test_empty_bytes(self) -> None:
        """Empty bytes produce a valid hash."""
        digest = compute_sha256(b"")
        assert len(digest) == 64


# ---------------------------------------------------------------------------
# Writer tests
# ---------------------------------------------------------------------------


class TestProvenanceWriter:
    """Writer inserts records via asyncpg pool."""

    @pytest.fixture
    def mock_pool(self) -> MagicMock:
        pool = MagicMock()
        conn = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=conn)
        ctx.__aexit__ = AsyncMock(return_value=None)
        pool.acquire.return_value = ctx
        return pool

    @pytest.fixture
    def writer(self, mock_pool: AsyncMock) -> ProvenanceWriter:
        return ProvenanceWriter(pool=mock_pool)

    @pytest.fixture
    def sample_record(self) -> ProvenanceRecord:
        return ProvenanceRecord(
            signal_id="sig-test",
            timestamp=datetime.now(timezone.utc),
            schema_version="2.0.0",
            git_sha="abc1234",
            input_bundle_sha256="input_hash",
            agent_verdicts=[
                AgentVerdict(
                    agent_name="technical",
                    state="READY",
                    score=50,
                    max_score=75,
                    direction="bullish",
                ),
            ],
            raw_confluence_score=150,
            normalised_score=68,
            decision="BUY",
            pipeline_confidence=0.85,
            signal_output_sha256="output_hash",
            cycle_latency_ms=120.0,
        )

    @pytest.mark.asyncio
    async def test_write_calls_execute(
        self,
        writer: ProvenanceWriter,
        mock_pool: AsyncMock,
        sample_record: ProvenanceRecord,
    ) -> None:
        """Write inserts a row via asyncpg."""
        await writer.write(sample_record)
        conn = mock_pool.acquire.return_value.__aenter__.return_value
        conn.execute.assert_called_once()
        args = conn.execute.call_args[0]
        assert args[1] == "sig-test"  # signal_id

    @pytest.mark.asyncio
    async def test_write_async_enqueues_record(
        self,
        writer: ProvenanceWriter,
        mock_pool: MagicMock,
        sample_record: ProvenanceRecord,
    ) -> None:
        """Write async enqueues a record via queue."""
        writer.write_async(sample_record)
        assert writer._queue.qsize() == 1
        await writer.close()


class TestGetGitSha:
    """Git SHA retrieval."""

    def test_returns_string(self) -> None:
        """Always returns a non-empty string."""
        sha = _get_git_sha()
        assert isinstance(sha, str)
        assert len(sha) > 0
