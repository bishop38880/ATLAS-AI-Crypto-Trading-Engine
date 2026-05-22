"""Agent Zero data models — Escore weights, records, and archive summaries.

All models are frozen Pydantic to enforce immutability in the
scoring and archival pipeline.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Escore Weights
# ---------------------------------------------------------------------------


class EscoreWeights(BaseModel):
    """Weights for the four Escore dimensions.

    Must sum to exactly 1.0 — enforced by a model validator.

    Attributes:
        recency: Weight for document age scoring.
        retrieval_frequency: Weight for how often the doc is retrieved.
        outcome_quality: Weight for trade outcome quality.
        contradiction_free: Weight for absence of contradictions.
    """

    model_config = ConfigDict(frozen=True)

    recency: float = 0.25
    retrieval_frequency: float = 0.30
    outcome_quality: float = 0.35
    contradiction_free: float = 0.10

    @model_validator(mode="after")
    def _weights_must_sum_to_one(self) -> EscoreWeights:
        """Validate that all four weights sum to exactly 1.0."""
        total = (
            self.recency
            + self.retrieval_frequency
            + self.outcome_quality
            + self.contradiction_free
        )
        if abs(total - 1.0) > 1e-9:
            msg = f"Weights must sum to 1.0, got {total:.10f}"
            raise ValueError(msg)
        return self


# ---------------------------------------------------------------------------
# Escore Record
# ---------------------------------------------------------------------------


class EscoreRecord(BaseModel):
    """Audit record for a single document's Escore calculation.

    Attributes:
        doc_id: Signal ID of the evaluated document.
        recency_score: Normalised recency component [0, 1].
        frequency_score: Normalised retrieval frequency component [0, 1].
        outcome_score: Normalised outcome quality component [0, 1].
        contradiction_score: Normalised contradiction-free component [0, 1].
        final_escore: Weighted composite score.
        should_archive: Whether the document falls below threshold.
        computed_at: UTC timestamp of computation.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str
    recency_score: float
    frequency_score: float
    outcome_score: float
    contradiction_score: float
    final_escore: float
    should_archive: bool
    computed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Archive Summary
# ---------------------------------------------------------------------------


class ArchiveSummary(BaseModel):
    """Summary of a single Agent Zero nightly run.

    Attributes:
        run_at: UTC timestamp when the run started.
        total_scored: Number of documents evaluated.
        archived_count: Number archived (below threshold).
        retained_count: Number retained (at or above threshold).
        avg_escore: Mean Escore across all evaluated documents.
        lowest_kept_escore: Minimum Escore among retained documents.
        highest_archived_escore: Maximum Escore among archived documents.
        error_count: Number of errors during the run.
    """

    model_config = ConfigDict(frozen=True)

    run_at: datetime
    total_scored: int
    archived_count: int
    retained_count: int
    avg_escore: float
    lowest_kept_escore: float
    highest_archived_escore: float
    error_count: int = 0


# ---------------------------------------------------------------------------
# State Machine Pass Result
# ---------------------------------------------------------------------------


class StateMachinePassResult(BaseModel):
    """Result of a nightly state machine pass."""

    model_config = ConfigDict(frozen=True)

    processed_pending_count: int
    expired_flagged_count: int
    aged_soft_deleted_count: int
