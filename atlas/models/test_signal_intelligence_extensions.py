"""Tests for IM-1 intelligence matrix extensions on SignalOutput.

Tests live alongside code (atlas/models/test_signal_intelligence_extensions.py).
Covers: AgentResult.score > 220 rejection, SignalOutput msgspec round-trip,
raw_confluence_score, calibrated_probability, deepseek_evaluation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import msgspec
import pytest
from pydantic import ValidationError

from atlas.models.signal import (
    AgentResult,
    SignalDirection,
    ActionBlock,
    CategoryScores,
    DeepSeekDecision,
    SignalDecision,
    SignalOutput,
    SubSignalResult,
)
from atlas.models.enums import CrossCorrelationGrade
from atlas.models.telemetry import TelemetryEvent


# ---------------------------------------------------------------------------
# Fixtures — shared test data builders
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 4, 21, 14, 0, 0, tzinfo=timezone.utc)
_LATER = _NOW + timedelta(minutes=30)


def _make_telemetry() -> TelemetryEvent:
    """Build a minimal TelemetryEvent for test fixtures.

    Returns:
        A valid TelemetryEvent instance.
    """
    return TelemetryEvent(
        cycle_id="cycle-001",
        cycle_latency_ms=120.5,
        agent_count=4,
        timestamp=_NOW,
    )


def _make_category_scores(total: int = 75) -> CategoryScores:
    """Build CategoryScores that sum to the given total.

    Args:
        total: Desired total score.

    Returns:
        A valid CategoryScores instance.
    """
    return CategoryScores(
        derivatives=20,
        onchain=15,
        technical=20,
        sentiment=10,
        context=10,
        total=total,
    )


def _make_action_block(side: str = "buy") -> ActionBlock:
    """Build a valid ActionBlock for test fixtures.

    Args:
        side: Trade direction ("buy" or "sell").

    Returns:
        A valid ActionBlock instance.
    """
    return ActionBlock(
        side=side,
        order_type="limit",
        price=Decimal("87250.00"),
        stop_loss=Decimal("85000.00"),
        take_profit=Decimal("92000.00"),
    )


def _make_signal(**overrides: object) -> SignalOutput:
    """Build a valid SignalOutput, merging any overrides.

    Args:
        **overrides: Fields to override on the default signal.

    Returns:
        A valid SignalOutput instance.
    """
    defaults: dict[str, object] = {
        "decision": SignalDecision.BUY,
        "asset": "BTCUSDT",
        "timeframe": "30m",
        "action": _make_action_block(),
        "expires_at": _LATER,
        "timestamp": _NOW,
        "score": 75,
        "confidence": 0.85,
        "category_scores": _make_category_scores(),
        "telemetry": _make_telemetry(),
    }
    defaults.update(overrides)
    return SignalOutput(**defaults)  # type: ignore[arg-type]


def _make_deepseek_decision() -> DeepSeekDecision:
    """Build a valid DeepSeekDecision for test fixtures.

    Returns:
        A valid DeepSeekDecision instance.
    """
    return DeepSeekDecision(
        decision=SignalDecision.BUY,
        confidence=Decimal("0.85"),
        cross_correlation_grade=CrossCorrelationGrade.ELEVATED,
        key_convergences=["Funding Z-score beyond 2.5 SD"],
        key_risks=["Resistance cluster at $89k"],
        reasoning="Strong convergence across agents.",
        would_change_if="Funding rate normalises.",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAgentResultScoreBounds:
    """AgentResult.score must be 0–220."""

    def test_score_above_220_raises(self) -> None:
        """AgentResult.score > 220 raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentResult(
                agent_name="derivatives",
                score=221,
                max_score=220,
            )

    def test_score_at_220_accepted(self) -> None:
        """AgentResult.score = 220 is valid."""
        result = AgentResult(
            agent_name="derivatives",
            score=220,
            max_score=220,
        )
        assert result.score == 220

    def test_score_at_0_accepted(self) -> None:
        """AgentResult.score = 0 is valid."""
        result = AgentResult(
            agent_name="derivatives",
            score=0,
            max_score=220,
        )
        assert result.score == 0

    def test_negative_score_rejected(self) -> None:
        """AgentResult.score < 0 raises ValidationError."""
        with pytest.raises(ValidationError):
            AgentResult(
                agent_name="derivatives",
                score=-1,
                max_score=220,
            )


class TestSignalOutputMsgspecRoundTrip:
    """SignalOutput round-trips via msgspec encode/decode."""

    def test_roundtrip_via_msgspec(self) -> None:
        """Encode with msgspec, decode, reconstruct — fields unchanged.

        Uses msgspec.json.encode(model.model_dump()) — NEVER
        model_dump_json() which is banned (bypasses msgspec).
        """
        signal = _make_signal()
        encoded = msgspec.json.encode(
            signal.model_dump(mode="json"),
        )
        decoded: dict = msgspec.json.decode(encoded)  # type: ignore[type-arg]
        reconstructed = SignalOutput(**decoded)

        assert reconstructed.signal_id == signal.signal_id
        assert reconstructed.decision == signal.decision
        assert reconstructed.asset == signal.asset
        assert reconstructed.score == signal.score
        assert reconstructed.confidence == signal.confidence

    def test_roundtrip_with_im1_fields(self) -> None:
        """Round-trip preserves IM-1 fields."""
        signal = _make_signal(
            raw_confluence_score=165,
            deepseek_evaluation=_make_deepseek_decision(),
            calibrated_probability=0.72,
            calibration_ece=0.03,
        )
        encoded = msgspec.json.encode(
            signal.model_dump(mode="json"),
        )
        decoded: dict = msgspec.json.decode(encoded)  # type: ignore[type-arg]
        reconstructed = SignalOutput(**decoded)

        assert reconstructed.raw_confluence_score == 165
        assert reconstructed.calibrated_probability == Decimal("0.72")
        assert reconstructed.calibration_ece == 0.03
        assert reconstructed.deepseek_evaluation is not None
        assert reconstructed.deepseek_evaluation.confidence == Decimal("0.85")


class TestRawConfluenceScore:
    """raw_confluence_score defaults to 0 and accepts 0–220."""

    def test_default_is_zero(self) -> None:
        """Default raw_confluence_score is 0."""
        signal = _make_signal()
        assert signal.raw_confluence_score == 0

    def test_accepts_range_0_to_220(self) -> None:
        """raw_confluence_score accepts 0 and 220."""
        low = _make_signal(raw_confluence_score=0)
        high = _make_signal(raw_confluence_score=220)
        assert low.raw_confluence_score == 0
        assert high.raw_confluence_score == 220

    def test_above_220_rejected(self) -> None:
        """raw_confluence_score > 220 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(raw_confluence_score=221)

    def test_below_0_rejected(self) -> None:
        """raw_confluence_score < 0 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(raw_confluence_score=-1)


class TestCalibratedProbability:
    """calibrated_probability defaults to None, accepts 0.0–1.0."""

    def test_default_is_none(self) -> None:
        """Default calibrated_probability is None."""
        signal = _make_signal()
        assert signal.calibrated_probability is None

    def test_accepts_0_to_1(self) -> None:
        """calibrated_probability accepts 0.0 and 1.0."""
        low = _make_signal(calibrated_probability=0.0)
        high = _make_signal(calibrated_probability=1.0)
        assert low.calibrated_probability == 0.0
        assert high.calibrated_probability == 1.0

    def test_above_1_rejected(self) -> None:
        """calibrated_probability > 1.0 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(calibrated_probability=1.5)


class TestDeepSeekEvaluation:
    """deepseek_evaluation defaults to None; populated path validates."""

    def test_default_is_none(self) -> None:
        """Default deepseek_evaluation is None."""
        signal = _make_signal()
        assert signal.deepseek_evaluation is None

    def test_populated_path_validates(self) -> None:
        """deepseek_evaluation with valid DeepSeekDecision is accepted."""
        decision = _make_deepseek_decision()
        signal = _make_signal(deepseek_evaluation=decision)
        assert signal.deepseek_evaluation is not None
        assert signal.deepseek_evaluation.decision is SignalDecision.BUY
        assert signal.deepseek_evaluation.cross_correlation_grade is (
            CrossCorrelationGrade.ELEVATED
        )


class TestAgentBreakdown:
    """agent_breakdown defaults to empty dict."""

    def test_default_is_empty_dict(self) -> None:
        """Default agent_breakdown is an empty dict."""
        signal = _make_signal()
        assert signal.agent_breakdown == {}

    def test_populated_with_agent_results(self) -> None:
        """agent_breakdown with AgentResult values is accepted."""
        sub_sig = SubSignalResult(
            value="+2.8 SD",
            flag="EXTREME_SHORT_CROWDING",
        )
        agent = AgentResult(
            agent_name="derivatives",
            score=62,
            max_score=80,
            weight=0.35,
            direction=SignalDirection.BULLISH,
            sub_signals={"funding_zscore": sub_sig},
        )
        signal = _make_signal(
            agent_breakdown={"derivatives": agent},
        )
        assert "derivatives" in signal.agent_breakdown
        deriv = signal.agent_breakdown["derivatives"]
        assert deriv.score == 62
        assert "funding_zscore" in deriv.sub_signals


class TestCalibrationEce:
    """calibration_ece defaults to None, accepts >= 0.0."""

    def test_default_is_none(self) -> None:
        """Default calibration_ece is None."""
        signal = _make_signal()
        assert signal.calibration_ece is None

    def test_accepts_zero(self) -> None:
        """calibration_ece = 0.0 is valid."""
        signal = _make_signal(calibration_ece=0.0)
        assert signal.calibration_ece == 0.0

    def test_accepts_positive(self) -> None:
        """calibration_ece = 0.15 is valid."""
        signal = _make_signal(calibration_ece=0.15)
        assert signal.calibration_ece == 0.15
