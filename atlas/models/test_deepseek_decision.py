"""Tests for DeepSeekDecision — IM-1 Task 6.

Tests live alongside code (atlas/models/test_deepseek_decision.py).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.models.enums import CrossCorrelationGrade
from atlas.models.signal import (
    DeepSeekDecision,
    SignalDecision,
    build_safe_fallback_decision,
)


def _make_deepseek_decision(**overrides: object) -> DeepSeekDecision:
    """Build a valid DeepSeekDecision, merging any overrides.

    Args:
        **overrides: Fields to override on the default decision.

    Returns:
        A valid DeepSeekDecision instance.
    """
    defaults: dict[str, object] = {
        "decision": SignalDecision.BUY,
        "confidence": 0.85,
        "cross_correlation_grade": CrossCorrelationGrade.ELEVATED,
        "key_convergences": ["Funding Z-score beyond 2.5 SD"],
        "key_risks": ["Resistance cluster at $89k"],
        "reasoning": "Strong cross-agent convergence on bullish signals.",
        "would_change_if": "Funding rate normalises below 1.5 SD.",
    }
    defaults.update(overrides)
    return DeepSeekDecision(**defaults)  # type: ignore[arg-type]


class TestDeepSeekDecisionConfidence:
    """Confidence must be 0.0–1.0."""

    def test_confidence_above_1_rejected(self) -> None:
        """confidence = 1.5 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_deepseek_decision(confidence=1.5)

    def test_confidence_below_0_rejected(self) -> None:
        """confidence = -0.1 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_deepseek_decision(confidence=-0.1)

    def test_confidence_at_boundaries(self) -> None:
        """confidence = 0.0 and 1.0 are valid."""
        low = _make_deepseek_decision(confidence=0.0)
        high = _make_deepseek_decision(confidence=1.0)
        assert low.confidence == 0.0
        assert high.confidence == 1.0


class TestDeepSeekDecisionEnumCoercion:
    """String-to-enum coercion for wire-format compatibility."""

    def test_string_coerced_to_enum(self) -> None:
        """cross_correlation_grade='STANDARD' (string) coerces to enum.

        This is intentional for wire-format compatibility. Pydantic v2
        coerces valid strings to enums by default. frozen=True only
        prevents post-instantiation mutation, not string coercion.
        """
        decision = _make_deepseek_decision(
            cross_correlation_grade="STANDARD",
        )
        assert decision.cross_correlation_grade is (
            CrossCorrelationGrade.STANDARD
        )
        assert isinstance(
            decision.cross_correlation_grade, CrossCorrelationGrade,
        )

    def test_decision_string_coerced_to_enum(self) -> None:
        """decision='Hold' (string) coerces to SignalDecision.HOLD."""
        decision = _make_deepseek_decision(decision="Hold")
        assert decision.decision is SignalDecision.HOLD

    def test_invalid_grade_string_rejected(self) -> None:
        """Invalid grade string raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_deepseek_decision(
                cross_correlation_grade="INVALID_GRADE",
            )


class TestBuildSafeFallbackDecision:
    """build_safe_fallback_decision returns HOLD/STANDARD/0.0."""

    def test_fallback_decision_is_hold(self) -> None:
        """Fallback returns SignalDecision.HOLD."""
        fallback = build_safe_fallback_decision("timeout")
        assert fallback.decision is SignalDecision.HOLD

    def test_fallback_grade_is_standard(self) -> None:
        """Fallback returns CrossCorrelationGrade.STANDARD."""
        fallback = build_safe_fallback_decision("timeout")
        assert fallback.cross_correlation_grade is (
            CrossCorrelationGrade.STANDARD
        )

    def test_fallback_confidence_is_zero(self) -> None:
        """Fallback returns confidence = 0.0."""
        fallback = build_safe_fallback_decision("timeout")
        assert fallback.confidence == 0.0

    def test_fallback_reason_in_reasoning(self) -> None:
        """Fallback includes the reason in reasoning field."""
        fallback = build_safe_fallback_decision("connection refused")
        assert "connection refused" in fallback.reasoning

    def test_fallback_convergences_are_api_failure(self) -> None:
        """Fallback key_convergences contain API_FAILURE_FALLBACK."""
        fallback = build_safe_fallback_decision("timeout")
        assert fallback.key_convergences == ["API_FAILURE_FALLBACK"]
        assert fallback.key_risks == ["API_FAILURE_FALLBACK"]


class TestDeepSeekDecisionFrozen:
    """DeepSeekDecision is immutable (frozen=True)."""

    def test_is_frozen(self) -> None:
        """Attribute reassignment raises ValidationError."""
        decision = _make_deepseek_decision()
        with pytest.raises(ValidationError):
            decision.confidence = 0.5  # type: ignore[misc]
