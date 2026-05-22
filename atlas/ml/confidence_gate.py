"""PipelineConfidenceCalculator — deterministic confidence from pipeline signals.

Computes a pipeline_confidence score (0.0–1.0) from five independent
dimensions already computed inside the pipeline each cycle. This class
has zero I/O — all inputs arrive pre-computed via PipelineConfidenceInputs.

RULE: This module must never import any provider, Redis client, LLM
client, or database module. If you find such an import, it is a bug.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from decimal import Decimal

from atlas.models.signal import ConfidenceDimensions, ConfidenceTier
from atlas.shared.config import PolarisSettings


class PipelineConfidenceInputs(BaseModel, frozen=True):
    """All inputs needed to compute pipeline_confidence.

    Collected by the Orchestrator from existing pipeline outputs.
    Zero new I/O required — all fields are already computed.
    """

    # From ConfluenceScorer (total_score on SignalOutput)
    conviction_point_estimate: int = Field(
        description="The raw total_score (0–220 scale)",
    )
    conviction_lower: int | None = Field(
        default=None,
        description="CQR lower bound. None if CQR not yet trained",
    )

    # From Orchestrator agent dispatch
    agents_dispatched: int = Field(
        ge=0,
        description="Total agents called this cycle",
    )
    agents_with_live_data: int = Field(
        ge=0,
        description="Agents that returned score > 0",
    )

    # From Validation Gate
    anomaly_flag_count: int = Field(
        default=0, ge=0,
        description="Count of anomaly flags this cycle",
    )
    consistency_warning_count: int = Field(
        default=0, ge=0,
        description="Count of cross-source divergence warnings",
    )

    # From HMM Regime Detector (Session 09)
    regime_transition_probability: float | None = Field(
        default=None,
        description="None if HMM not yet trained",
    )

    # From Validation Gate cross-source check
    max_price_divergence_pct: float | None = Field(
        default=None,
        description="None if only one provider active",
    )


class PipelineConfidenceCalculator:
    """Computes deterministic pipeline_confidence from existing signals.

    Pure function — no I/O, no providers, no LLM calls, no Redis.
    All inputs arrive via PipelineConfidenceInputs. Thresholds are
    read from PolarisSettings at instantiation.
    """

    # Dimension weights — must sum to 1.0
    WEIGHTS: dict[str, float] = {
        "conviction_tightness": 0.30,
        "agent_coverage":       0.25,
        "gate_cleanliness":     0.20,
        "regime_stability":     0.15,
        "provider_agreement":   0.10,
    }

    def __init__(self, settings: PolarisSettings) -> None:
        """Initialize with gate thresholds from config.

        Args:
            settings: PolarisSettings — thresholds read once at init.
        """
        self._skip_threshold = settings.confidence_gate_skip_threshold
        self._reduced_threshold = settings.confidence_gate_reduced_threshold
        self._human_conviction_min = settings.confidence_gate_human_conviction_min
        self._human_confidence_max = settings.confidence_gate_human_confidence_max

    def calculate(
        self,
        inputs: PipelineConfidenceInputs,
    ) -> tuple[float, ConfidenceDimensions]:
        """Compute pipeline_confidence and dimension breakdown.

        Args:
            inputs: Pre-computed pipeline signals from Orchestrator.

        Returns:
            Tuple of (pipeline_confidence, dimensions).
        """
        dims = self._build_dimensions(inputs)
        confidence = self._weighted_sum(dims)
        return round(float(confidence), 4), dims

    def classify_tier(
        self,
        confidence: float,
        conviction: int,
    ) -> tuple[ConfidenceTier, float, bool]:
        """Determine confidence tier, modifier, and human review flag.

        Args:
            confidence: Output of calculate().
            conviction: Raw conviction point estimate (total_score).

        Returns:
            Tuple of (tier, position_size_modifier, human_review_flag).
        """
        if confidence < self._skip_threshold:
            return ConfidenceTier.SKIP, 0.0, False

        human_review = self._check_human_review(confidence, conviction)

        if confidence < self._reduced_threshold:
            return ConfidenceTier.REDUCED, 0.50, human_review

        return ConfidenceTier.STANDARD, 1.0, human_review

    # ── Private helpers ──────────────────────────────────────────────

    def _build_dimensions(
        self, inputs: PipelineConfidenceInputs,
    ) -> ConfidenceDimensions:
        """Build ConfidenceDimensions from raw inputs."""
        return ConfidenceDimensions(
            conviction_tightness=Decimal(str(self._conviction_tightness(
                inputs.conviction_point_estimate,
                inputs.conviction_lower,
            ))),
            agent_coverage=Decimal(str(self._agent_coverage(
                inputs.agents_with_live_data,
                inputs.agents_dispatched,
            ))),
            gate_cleanliness=Decimal(str(self._gate_cleanliness(
                inputs.anomaly_flag_count,
                inputs.consistency_warning_count,
            ))),
            regime_stability=Decimal(str(self._regime_stability(
                inputs.regime_transition_probability,
            ))),
            provider_agreement=Decimal(str(self._provider_agreement(
                inputs.max_price_divergence_pct,
            ))),
        )

    def _weighted_sum(self, dims: ConfidenceDimensions) -> float:
        """Compute weighted sum of all dimensions."""
        return (
            self.WEIGHTS["conviction_tightness"] * float(dims.conviction_tightness)
            + self.WEIGHTS["agent_coverage"] * float(dims.agent_coverage)
            + self.WEIGHTS["gate_cleanliness"] * float(dims.gate_cleanliness)
            + self.WEIGHTS["regime_stability"] * float(dims.regime_stability)
            + self.WEIGHTS["provider_agreement"] * float(dims.provider_agreement)
        )

    def _check_human_review(
        self, confidence: float, conviction: int,
    ) -> bool:
        """True only for the genuine paradox: high conviction + low confidence."""
        return (
            conviction >= self._human_conviction_min
            and confidence < self._human_confidence_max
        )

    # ── Dimension calculators ────────────────────────────────────────

    def _conviction_tightness(
        self, point_estimate: int, lower: int | None,
    ) -> float:
        """Width of conviction interval relative to point estimate.

        If CQR not trained (lower is None), return 0.5 (neutral).
        Tight interval (lower close to point) → near 1.0.
        Maximum meaningful spread capped at 220-point scale.
        """
        if lower is None or point_estimate == 0:
            return 0.5
        spread = point_estimate - lower
        return max(0.0, min(1.0, 1.0 - (spread / 220.0)))

    def _agent_coverage(self, live: int, dispatched: int) -> float:
        """Fraction of agents that returned live data this cycle."""
        if dispatched == 0:
            return 0.0
        return min(1.0, live / dispatched)

    def _gate_cleanliness(
        self, anomaly_flags: int, consistency_warnings: int,
    ) -> float:
        """Absence of Validation Gate flags.

        Linear penalty per flag. 5+ total flags → 0.0. 0 flags → 1.0.
        """
        total_flags = anomaly_flags + consistency_warnings
        return max(0.0, 1.0 - (total_flags / 5.0))

    def _regime_stability(self, transition_prob: float | None) -> float:
        """Stability of current regime (inverse of transition probability).

        If HMM not trained, return 0.5 (neutral — no information).
        """
        if transition_prob is None:
            return 0.5
        return max(0.0, 1.0 - float(transition_prob))

    def _provider_agreement(
        self, max_divergence_pct: float | None,
    ) -> float:
        """Cross-source provider agreement.

        Single provider (no cross-check) → 0.7 (slight penalty).
        0% divergence → 1.0. >= 2% divergence → 0.0.
        """
        if max_divergence_pct is None:
            return 0.7
        return max(0.0, 1.0 - (max_divergence_pct / 2.0))
