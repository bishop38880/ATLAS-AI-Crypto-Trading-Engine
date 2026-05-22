"""Tests for PipelineConfidenceCalculator — Session 20.

Covers all tiers, edge cases, dimension bounds, and backward compatibility.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from atlas.ml.confidence_gate import (
    PipelineConfidenceCalculator,
    PipelineConfidenceInputs,
)
from atlas.models.signal import ConfidenceTier
from atlas.shared.config import PolarisSettings


def _make_settings() -> PolarisSettings:
    """Build a PolarisSettings with default confidence gate thresholds."""
    return PolarisSettings(
        confidence_gate_skip_threshold=0.40,
        confidence_gate_reduced_threshold=0.65,
        confidence_gate_human_conviction_min=140,
        confidence_gate_human_confidence_max=0.50,
    )


def _make_calculator() -> PipelineConfidenceCalculator:
    """Build a calculator with default thresholds."""
    return PipelineConfidenceCalculator(_make_settings())


class TestPerfectInputs:
    """Test 1: Perfect inputs → confidence near 1.0."""

    def test_high_confidence_on_perfect_inputs(self) -> None:
        """All dimensions maximal → confidence >= 0.90."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=160,
            conviction_lower=155,
            agents_dispatched=8,
            agents_with_live_data=8,
            anomaly_flag_count=0,
            consistency_warning_count=0,
            regime_transition_probability=0.05,
            max_price_divergence_pct=0.0,
        )
        confidence, dims = calc.calculate(inputs)
        assert confidence >= 0.90, f"Expected >= 0.90, got {confidence}"
        tier, modifier, _ = calc.classify_tier(confidence, 160)
        assert tier == ConfidenceTier.STANDARD
        assert modifier == 1.0


class TestDegradedInputsSkip:
    """Test 2: Degraded inputs → SKIP tier."""

    def test_skip_tier_on_degraded_inputs(self) -> None:
        """Wide interval, few agents, many flags → SKIP."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=145,
            conviction_lower=50,
            agents_dispatched=8,
            agents_with_live_data=2,
            anomaly_flag_count=4,
            consistency_warning_count=2,
            regime_transition_probability=0.85,
            max_price_divergence_pct=1.8,
        )
        confidence, _ = calc.calculate(inputs)
        tier, modifier, _ = calc.classify_tier(confidence, 145)
        assert tier == ConfidenceTier.SKIP
        assert modifier == 0.0


class TestModerateInputsReduced:
    """Test 3: Moderate inputs → REDUCED tier."""

    def test_reduced_tier_on_moderate_inputs(self) -> None:
        """Mid-range across dimensions → REDUCED tier."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=130,
            conviction_lower=90,
            agents_dispatched=8,
            agents_with_live_data=5,
            anomaly_flag_count=2,
            consistency_warning_count=1,
            regime_transition_probability=0.45,
            max_price_divergence_pct=0.8,
        )
        confidence, _ = calc.calculate(inputs)
        tier, modifier, _ = calc.classify_tier(confidence, 130)
        assert tier == ConfidenceTier.REDUCED, f"Expected REDUCED, got {tier} (confidence={confidence})"
        assert modifier == 0.50


class TestHumanReviewParadox:
    """Test 4: High conviction + low confidence → human review."""

    def test_human_review_flag_set(self) -> None:
        """conviction >= 140 AND confidence < 0.50 → True."""
        calc = _make_calculator()
        # Directly test classify_tier with the paradox values
        tier, modifier, human_review = calc.classify_tier(
            confidence=0.44, conviction=155,
        )
        assert human_review is True
        assert tier == ConfidenceTier.REDUCED

    def test_no_human_review_when_confidence_adequate(self) -> None:
        """conviction >= 140 but confidence >= 0.50 → False."""
        calc = _make_calculator()
        _, _, human_review = calc.classify_tier(
            confidence=0.55, conviction=155,
        )
        assert human_review is False

    def test_no_human_review_on_skip(self) -> None:
        """SKIP tier never triggers human review."""
        calc = _make_calculator()
        tier, _, human_review = calc.classify_tier(
            confidence=0.30, conviction=155,
        )
        assert tier == ConfidenceTier.SKIP
        assert human_review is False


class TestNeutralDefaults:
    """Test 5: Missing CQR/HMM/provider → neutral defaults."""

    def test_missing_cqr_gives_neutral_tightness(self) -> None:
        """conviction_lower=None → conviction_tightness == 0.5."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=100,
            conviction_lower=None,
            agents_dispatched=5,
            agents_with_live_data=5,
        )
        _, dims = calc.calculate(inputs)
        assert dims.conviction_tightness == 0.5

    def test_missing_hmm_gives_neutral_stability(self) -> None:
        """transition_probability=None → regime_stability == 0.5."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=100,
            agents_dispatched=5,
            agents_with_live_data=5,
            regime_transition_probability=None,
        )
        _, dims = calc.calculate(inputs)
        assert dims.regime_stability == 0.5

    def test_single_provider_gives_partial_agreement(self) -> None:
        """max_price_divergence_pct=None → provider_agreement == 0.7."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=100,
            agents_dispatched=5,
            agents_with_live_data=5,
            max_price_divergence_pct=None,
        )
        _, dims = calc.calculate(inputs)
        assert dims.provider_agreement == Decimal("0.7")


class TestZeroAgents:
    """Test 6: Zero agents dispatched → no ZeroDivisionError."""

    def test_zero_agents_gives_zero_coverage(self) -> None:
        """agents_dispatched=0 → agent_coverage == 0.0."""
        calc = _make_calculator()
        inputs = PipelineConfidenceInputs(
            conviction_point_estimate=100,
            agents_dispatched=0,
            agents_with_live_data=0,
        )
        _, dims = calc.calculate(inputs)
        assert dims.agent_coverage == 0.0


class TestWeightsIntegrity:
    """Test 7: Dimension weights sum to 1.0."""

    def test_weights_sum_to_one(self) -> None:
        """Config integrity: weights must sum to exactly 1.0."""
        total = sum(PipelineConfidenceCalculator.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"


class TestDimensionBounds:
    """Test 8: All dimension scores in [0.0, 1.0] for valid inputs."""

    @pytest.mark.parametrize("point_estimate", [0, 50, 100, 220])
    @pytest.mark.parametrize("lower", [None, 0, 50, 100])
    def test_conviction_tightness_bounded(
        self, point_estimate: int, lower: int | None,
    ) -> None:
        """Conviction tightness always in [0, 1]."""
        calc = _make_calculator()
        val = calc._conviction_tightness(point_estimate, lower)
        assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"

    @pytest.mark.parametrize("live,dispatched", [
        (0, 0), (0, 5), (3, 5), (5, 5), (10, 5),
    ])
    def test_agent_coverage_bounded(
        self, live: int, dispatched: int,
    ) -> None:
        """Agent coverage always in [0, 1]."""
        calc = _make_calculator()
        val = calc._agent_coverage(live, dispatched)
        assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"

    @pytest.mark.parametrize("flags,warnings", [
        (0, 0), (2, 1), (5, 0), (0, 5), (10, 10),
    ])
    def test_gate_cleanliness_bounded(
        self, flags: int, warnings: int,
    ) -> None:
        """Gate cleanliness always in [0, 1]."""
        calc = _make_calculator()
        val = calc._gate_cleanliness(flags, warnings)
        assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"

    @pytest.mark.parametrize("prob", [None, 0.0, 0.5, 1.0, 1.5])
    def test_regime_stability_bounded(self, prob: float | None) -> None:
        """Regime stability always in [0, 1]."""
        calc = _make_calculator()
        val = calc._regime_stability(prob)
        assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"

    @pytest.mark.parametrize("div", [None, 0.0, 1.0, 2.0, 5.0])
    def test_provider_agreement_bounded(self, div: float | None) -> None:
        """Provider agreement always in [0, 1]."""
        calc = _make_calculator()
        val = calc._provider_agreement(div)
        assert 0.0 <= val <= 1.0, f"Out of bounds: {val}"
