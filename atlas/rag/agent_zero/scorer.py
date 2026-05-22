"""Escore Calculator — computes Memory Usefulness Score for RAG documents.

Evaluates each document across four dimensions:
    1. Recency — how recently the document was created.
    2. Retrieval Frequency — how often the document is retrieved.
    3. Outcome Quality — trade outcome quality (WIN/LOSS/SCRATCH).
    4. Contradiction Freedom — absence of contradictory signals.

The weighted composite score determines whether a document should be
archived (soft-deleted) from active memory.
"""

from __future__ import annotations

from datetime import datetime, timezone

from atlas.rag.agent_zero.models import EscoreRecord, EscoreWeights


# ---------------------------------------------------------------------------
# Recency tier boundaries (in days)
# ---------------------------------------------------------------------------

_RECENCY_TIERS: list[tuple[float, float]] = [
    (1.0, 1.0),     # < 24h
    (7.0, 0.8),     # 1–7 days
    (30.0, 0.5),    # 7–30 days
    (90.0, 0.3),    # 30–90 days
]
_RECENCY_DEFAULT = 0.1  # > 90 days


# ---------------------------------------------------------------------------
# Frequency tier boundaries
# ---------------------------------------------------------------------------

_FREQUENCY_TIERS: list[tuple[int, float]] = [
    (10, 1.0),   # ≥ 10 retrievals
    (5, 0.7),    # 5–9 retrievals
    (2, 0.4),    # 2–4 retrievals
    (1, 0.2),    # exactly 1 retrieval
]
_FREQUENCY_DEFAULT = 0.0  # 0 retrievals


# ---------------------------------------------------------------------------
# Outcome label → score mapping
# ---------------------------------------------------------------------------

_OUTCOME_SCORES: dict[str, float] = {
    "WIN": 1.0,
    "LOSS": 0.0,
    "SCRATCH": 0.5,
}
_OUTCOME_PENDING = 0.5  # NULL / unknown


# ---------------------------------------------------------------------------
# EscoreCalculator
# ---------------------------------------------------------------------------


class EscoreCalculator:
    """Calculates the Escore (Memory Usefulness Score) for a document.

    Args:
        weights: Dimension weights (must sum to 1.0).
        threshold: Score below which documents are archived.
    """

    def __init__(
        self,
        weights: EscoreWeights | None = None,
        threshold: float = 0.75,
    ) -> None:
        """Initialise with scoring weights and archive threshold."""
        self._weights = weights or EscoreWeights()
        self._threshold = threshold

    def calculate(self, doc_metadata: dict[str, object]) -> EscoreRecord:
        """Compute the Escore for a single document.

        Args:
            doc_metadata: Dict with keys: signal_id, created_at,
                retrieval_count, outcome_label, contradiction_count.

        Returns:
            An EscoreRecord with all dimension scores and final verdict.
        """
        recency = self._compute_recency(doc_metadata.get("created_at"))
        frequency = self._compute_frequency(doc_metadata.get("retrieval_count", 0))
        outcome = self._compute_outcome(doc_metadata.get("outcome_label"))
        contradiction = self._compute_contradiction(
            doc_metadata.get("contradiction_count", 0),
        )

        w = self._weights
        final = (
            w.recency * recency
            + w.retrieval_frequency * frequency
            + w.outcome_quality * outcome
            + w.contradiction_free * contradiction
        )

        return EscoreRecord(
            doc_id=str(doc_metadata.get("signal_id", "")),
            recency_score=recency,
            frequency_score=frequency,
            outcome_score=outcome,
            contradiction_score=contradiction,
            final_escore=round(final, 4),
            should_archive=final < self._threshold,
            computed_at=datetime.now(timezone.utc),
        )

    # ── Dimension scorers ─────────────────────────────────────────────

    def _compute_recency(self, created_at: object) -> float:
        """Score document recency using tiered day-based decay.

        Args:
            created_at: Document creation timestamp (datetime or None).

        Returns:
            Recency score in [0.1, 1.0].
        """
        if not isinstance(created_at, datetime):
            return _RECENCY_DEFAULT

        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = (now - created_at).total_seconds() / 86400.0

        for max_days, score in _RECENCY_TIERS:
            if age_days < max_days:
                return score
        return _RECENCY_DEFAULT

    def _compute_frequency(self, retrieval_count: object) -> float:
        """Score document retrieval frequency.

        Args:
            retrieval_count: Number of times retrieved (int or None).

        Returns:
            Frequency score in [0.0, 1.0].
        """
        count = int(retrieval_count) if isinstance(retrieval_count, (int, float, str)) else 0
        for min_count, score in _FREQUENCY_TIERS:
            if count >= min_count:
                return score
        return _FREQUENCY_DEFAULT

    def _compute_outcome(self, outcome_label: object) -> float:
        """Score trade outcome quality.

        Args:
            outcome_label: One of WIN, LOSS, SCRATCH, or None.

        Returns:
            Outcome score in [0.0, 1.0]. Defaults to 0.5 for pending.
        """
        if outcome_label is None or not isinstance(outcome_label, str):
            return _OUTCOME_PENDING
        return _OUTCOME_SCORES.get(outcome_label, _OUTCOME_PENDING)

    def _compute_contradiction(self, contradiction_count: object) -> float:
        """Score absence of contradictions.

        Args:
            contradiction_count: Number of contradictions (int or None).

        Returns:
            Contradiction-free score: 0→1.0, 1→0.5, >1→0.0.
        """
        count = int(contradiction_count) if isinstance(contradiction_count, (int, float, str)) else 0
        if count == 0:
            return 1.0
        if count == 1:
            return 0.5
        return 0.0
