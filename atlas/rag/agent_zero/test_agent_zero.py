"""Tests for Agent Zero — Escore calculation, archival, and scheduler resilience.

Covers:
    - EscoreWeights model_validator rejects invalid sums
    - EscoreWeights accepts exact 1.0 sum
    - ArchiveSummary is frozen
    - Document >90d old with 0 retrievals → archived
    - Recent document (<24h) with high retrieval count → retained
    - LOSS outcome drags Escore below threshold
    - PENDING (None) outcome returns 0.5
    - Contradiction count >1 gives 0.0 score
    - Scheduler failure doesn't crash main loop
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.rag.agent_zero.models import ArchiveSummary, EscoreWeights
from atlas.rag.agent_zero.scorer import EscoreCalculator
from atlas.rag.agent_zero.scheduler import AgentZeroScheduler


# ---------------------------------------------------------------------------
# EscoreWeights tests
# ---------------------------------------------------------------------------


class TestEscoreWeights:
    """Tests for the EscoreWeights frozen Pydantic model."""

    def test_default_weights_sum_to_one(self) -> None:
        """Default weights (0.25 + 0.30 + 0.35 + 0.10) = 1.0."""
        w = EscoreWeights()
        total = w.recency + w.retrieval_frequency + w.outcome_quality + w.contradiction_free
        assert abs(total - 1.0) < 1e-9

    def test_custom_valid_weights_accepted(self) -> None:
        """Custom weights summing to 1.0 are accepted."""
        w = EscoreWeights(
            recency=0.40,
            retrieval_frequency=0.20,
            outcome_quality=0.30,
            contradiction_free=0.10,
        )
        assert w.recency == 0.40

    def test_invalid_weights_rejected(self) -> None:
        """Weights summing to != 1.0 raise ValueError."""
        with pytest.raises(ValueError, match="Weights must sum to 1.0"):
            EscoreWeights(
                recency=0.50,
                retrieval_frequency=0.30,
                outcome_quality=0.35,
                contradiction_free=0.10,
            )

    def test_weights_are_frozen(self) -> None:
        """EscoreWeights is immutable — mutation must raise."""
        w = EscoreWeights()
        with pytest.raises(Exception):
            w.recency = 0.99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ArchiveSummary tests
# ---------------------------------------------------------------------------


class TestArchiveSummary:
    """Tests for the ArchiveSummary frozen model."""

    def test_archive_summary_is_frozen(self) -> None:
        """ArchiveSummary is immutable — mutation must raise."""
        summary = ArchiveSummary(
            run_at=datetime.now(timezone.utc),
            total_scored=10,
            archived_count=3,
            retained_count=7,
            avg_escore=0.80,
            lowest_kept_escore=0.76,
            highest_archived_escore=0.74,
        )
        with pytest.raises(Exception):
            summary.total_scored = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# EscoreCalculator tests
# ---------------------------------------------------------------------------


class TestEscoreCalculator:
    """Tests for the Escore scoring engine."""

    def test_old_document_zero_retrievals_archived(self) -> None:
        """Document >90d old with 0 retrievals → Escore < 0.75 → archived."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "old-doc-001",
            "created_at": now - timedelta(days=100),
            "outcome_label": None,
            "retrieval_count": 0,
            "contradiction_count": 0,
        }
        record = calc.calculate(doc)

        # recency=0.1, frequency=0.0, outcome=0.5, contradiction=1.0
        # = 0.25*0.1 + 0.30*0.0 + 0.35*0.5 + 0.10*1.0
        # = 0.025 + 0.0 + 0.175 + 0.10 = 0.30
        assert record.should_archive is True
        assert record.final_escore < 0.75

    def test_recent_high_retrieval_retained(self) -> None:
        """Recent document (<24h) with ≥10 retrievals → retained."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "fresh-doc-001",
            "created_at": now - timedelta(hours=2),
            "outcome_label": "WIN",
            "retrieval_count": 15,
            "contradiction_count": 0,
        }
        record = calc.calculate(doc)

        # recency=1.0, frequency=1.0, outcome=1.0, contradiction=1.0
        # = 0.25*1.0 + 0.30*1.0 + 0.35*1.0 + 0.10*1.0 = 1.0
        assert record.should_archive is False
        assert record.final_escore == 1.0

    def test_loss_outcome_drags_below_threshold(self) -> None:
        """LOSS outcome (0.0) significantly lowers the Escore."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "loss-doc-001",
            "created_at": now - timedelta(days=5),
            "outcome_label": "LOSS",
            "retrieval_count": 3,
            "contradiction_count": 0,
        }
        record = calc.calculate(doc)

        # recency=0.8, frequency=0.4, outcome=0.0, contradiction=1.0
        # = 0.25*0.8 + 0.30*0.4 + 0.35*0.0 + 0.10*1.0
        # = 0.20 + 0.12 + 0.0 + 0.10 = 0.42
        assert record.should_archive is True
        assert record.final_escore < 0.75
        assert record.outcome_score == 0.0

    def test_pending_outcome_returns_half(self) -> None:
        """None outcome_label (PENDING) returns 0.5."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "pending-doc-001",
            "created_at": now - timedelta(hours=1),
            "outcome_label": None,
            "retrieval_count": 10,
            "contradiction_count": 0,
        }
        record = calc.calculate(doc)
        assert record.outcome_score == 0.5

    def test_high_contradiction_zeros_score(self) -> None:
        """Contradiction count >1 gives contradiction_score = 0.0."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "contra-doc-001",
            "created_at": now - timedelta(hours=1),
            "outcome_label": "WIN",
            "retrieval_count": 10,
            "contradiction_count": 3,
        }
        record = calc.calculate(doc)
        assert record.contradiction_score == 0.0

    def test_single_contradiction_gives_half(self) -> None:
        """Exactly 1 contradiction gives 0.5."""
        calc = EscoreCalculator(threshold=0.75)
        now = datetime.now(timezone.utc)
        doc = {
            "signal_id": "single-contra-001",
            "created_at": now - timedelta(hours=1),
            "outcome_label": "WIN",
            "retrieval_count": 10,
            "contradiction_count": 1,
        }
        record = calc.calculate(doc)
        assert record.contradiction_score == 0.5

    def test_scratch_outcome_returns_half(self) -> None:
        """SCRATCH outcome → 0.5 score."""
        calc = EscoreCalculator(threshold=0.75)
        doc = {
            "signal_id": "scratch-001",
            "created_at": datetime.now(timezone.utc),
            "outcome_label": "SCRATCH",
            "retrieval_count": 0,
            "contradiction_count": 0,
        }
        record = calc.calculate(doc)
        assert record.outcome_score == 0.5

    def test_recency_tiers_are_correct(self) -> None:
        """Verify all recency tier boundaries."""
        calc = EscoreCalculator()
        now = datetime.now(timezone.utc)

        # < 24h → 1.0
        assert calc._compute_recency(now - timedelta(hours=12)) == 1.0
        # 1-7 days → 0.8
        assert calc._compute_recency(now - timedelta(days=3)) == 0.8
        # 7-30 days → 0.5
        assert calc._compute_recency(now - timedelta(days=15)) == 0.5
        # 30-90 days → 0.3
        assert calc._compute_recency(now - timedelta(days=60)) == 0.3
        # >90 days → 0.1
        assert calc._compute_recency(now - timedelta(days=120)) == 0.1

    def test_frequency_tiers_are_correct(self) -> None:
        """Verify all frequency tier boundaries."""
        calc = EscoreCalculator()

        assert calc._compute_frequency(10) == 1.0
        assert calc._compute_frequency(15) == 1.0
        assert calc._compute_frequency(5) == 0.7
        assert calc._compute_frequency(9) == 0.7
        assert calc._compute_frequency(2) == 0.4
        assert calc._compute_frequency(4) == 0.4
        assert calc._compute_frequency(1) == 0.2
        assert calc._compute_frequency(0) == 0.0


# ---------------------------------------------------------------------------
# AgentZeroScheduler tests
# ---------------------------------------------------------------------------


class TestAgentZeroScheduler:
    """Tests for scheduler resilience."""

    @pytest.mark.asyncio
    async def test_scheduler_failure_does_not_crash(self) -> None:
        """If run_nightly_cycle raises, the scheduler logs and continues."""
        mock_archiver = MagicMock()
        mock_archiver.run_nightly_cycle = AsyncMock(
            side_effect=ConnectionError("DB locked"),
        )

        scheduler = AgentZeroScheduler(
            archiver=mock_archiver,
            target_hour_utc=2,
        )

        # Call _run_safe directly — it should NOT raise
        await scheduler._run_safe()
        mock_archiver.run_nightly_cycle.assert_called_once()
        assert scheduler._last_run is not None
        assert scheduler._last_run["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_scheduler_success_records_summary(self) -> None:
        """Successful cycle stores scored/archived counts on last_run."""
        from atlas.rag.agent_zero.models import ArchiveSummary

        mock_archiver = MagicMock()
        mock_archiver.run_nightly_cycle = AsyncMock(
            return_value=ArchiveSummary(
                run_at=datetime.now(timezone.utc),
                total_scored=12,
                archived_count=3,
                retained_count=9,
                avg_escore=0.81,
                lowest_kept_escore=0.76,
                highest_archived_escore=0.74,
                error_count=0,
            ),
        )

        scheduler = AgentZeroScheduler(archiver=mock_archiver, target_hour_utc=2)
        await scheduler._run_safe()

        assert scheduler._last_run is not None
        assert scheduler._last_run["status"] == "PASSED"
        assert scheduler._last_run["documentsScored"] == 12
        assert scheduler._last_run["archived"] == 3

    @pytest.mark.asyncio
    async def test_scheduler_start_creates_task(self) -> None:
        """start() creates a background task."""
        mock_archiver = MagicMock()
        mock_archiver.run_nightly_cycle = AsyncMock()

        scheduler = AgentZeroScheduler(archiver=mock_archiver)
        scheduler.start()

        assert scheduler._running is True
        assert len(scheduler._background_tasks) == 1

        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_scheduler_stop_cancels_tasks(self) -> None:
        """stop() sets _running=False and cancels tasks."""
        mock_archiver = MagicMock()
        mock_archiver.run_nightly_cycle = AsyncMock()

        scheduler = AgentZeroScheduler(archiver=mock_archiver)
        scheduler.start()
        await scheduler.stop()

        assert scheduler._running is False

    def test_seconds_to_target_returns_positive(self) -> None:
        """_seconds_to_target always returns positive seconds."""
        mock_archiver = MagicMock()
        scheduler = AgentZeroScheduler(archiver=mock_archiver)
        seconds = scheduler._seconds_to_target()
        assert seconds > 0
        assert seconds <= 86400  # max 24 hours
