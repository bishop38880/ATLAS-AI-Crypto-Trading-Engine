"""Tests for SignalOutput schema — Session 00 quality gate.

Tests live alongside code (atlas/models/test_signal.py, not tests/).
Covers all 11 test cases from the Session 00 quality gates.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError
from atlas.shared.serialisation import decode_json, encode_json

from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
    AgentCategory,
    AgentTier,
    AgentState,
)
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestActionDecisionConsistency:
    """Actionable decisions must have an ActionBlock; others must not."""

    def test_buy_requires_action_block(self) -> None:
        """Buy decision with action=None raises ValidationError."""
        with pytest.raises(ValidationError, match="requires an ActionBlock"):
            _make_signal(
                decision=SignalDecision.BUY,
                action=None,
            )

    def test_hold_requires_no_action(self) -> None:
        """Hold decision with a non-None action raises ValidationError."""
        with pytest.raises(ValidationError, match="must have action=None"):
            _make_signal(
                decision=SignalDecision.HOLD,
                action=_make_action_block(),
            )

    def test_hold_accepts_none_action(self) -> None:
        """Hold decision with action=None is valid."""
        signal = _make_signal(
            decision=SignalDecision.HOLD,
            action=None,
        )
        assert signal.action is None

    def test_no_position_requires_no_action(self) -> None:
        """No Position decision with action=None is valid."""
        signal = _make_signal(
            decision=SignalDecision.NO_POSITION,
            action=None,
        )
        assert signal.decision == SignalDecision.NO_POSITION


class TestExpiresAt:
    """expires_at must be strictly after timestamp."""

    def test_expires_before_timestamp_rejected(self) -> None:
        """expires_at before timestamp raises ValidationError."""
        with pytest.raises(
            ValidationError,
            match="expires_at must be strictly after timestamp",
        ):
            _make_signal(
                expires_at=_NOW - timedelta(minutes=5),
            )

    def test_expires_equal_to_timestamp_rejected(self) -> None:
        """expires_at equal to timestamp raises ValidationError."""
        with pytest.raises(
            ValidationError,
            match="expires_at must be strictly after timestamp",
        ):
            _make_signal(expires_at=_NOW)


class TestScoreClamping:
    """Score must be within 0-100 range."""

    def test_score_above_100_rejected(self) -> None:
        """Score > 100 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(score=101)

    def test_score_below_0_rejected(self) -> None:
        """Score < 0 raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(score=-1)

    def test_score_at_boundaries_accepted(self) -> None:
        """Scores of 0 and 100 are valid."""
        signal_low = _make_signal(
            decision=SignalDecision.NO_POSITION,
            action=None,
            score=0,
        )
        assert signal_low.score == 0

        signal_high = _make_signal(score=100)
        assert signal_high.score == 100


class TestInvalidDecision:
    """Only the six defined SignalDecision values are accepted."""

    def test_invalid_decision_string_rejected(self) -> None:
        """An invalid decision string raises ValidationError."""
        with pytest.raises(ValidationError):
            _make_signal(decision="Invalid Decision")


class TestCascadeLinkage:
    """Cascade-triggered signals must carry a hydra_event_id."""

    def test_cascade_triggered_requires_event_id(self) -> None:
        """cascade_triggered=True without event_id raises error."""
        with pytest.raises(
            ValidationError,
            match="is_cascade_triggered=True requires hydra_event_id",
        ):
            _make_signal(
                is_cascade_triggered=True,
                hydra_event_id=None,
            )

    def test_cascade_triggered_with_event_id_valid(self) -> None:
        """cascade_triggered=True with event_id is valid."""
        signal = _make_signal(
            is_cascade_triggered=True,
            hydra_event_id="hydra-cascade-001",
        )
        assert signal.is_cascade_triggered is True
        assert signal.hydra_event_id == "hydra-cascade-001"


class TestActionBlockDecimal:
    """ActionBlock price fields must be Decimal."""

    def test_price_fields_are_decimal(self) -> None:
        """price, stop_loss, take_profit are stored as Decimal."""
        action = _make_action_block()
        assert isinstance(action.price, Decimal)
        assert isinstance(action.stop_loss, Decimal)
        assert isinstance(action.take_profit, Decimal)


class TestActionBlockNoAmount:
    """ActionBlock must NOT have an amount field."""

    def test_no_amount_field_on_action_block(self) -> None:
        """ActionBlock.model_fields does not contain 'amount'."""
        assert "amount" not in ActionBlock.model_fields


class TestSignalId:
    """signal_id is auto-generated as valid UUID4 if not provided."""

    def test_signal_id_auto_generated(self) -> None:
        """Default signal_id is a valid UUID4 string."""
        signal = _make_signal()
        parsed = UUID(signal.signal_id, version=4)
        assert str(parsed) == signal.signal_id

    def test_signal_id_custom_value_preserved(self) -> None:
        """Explicitly provided signal_id is preserved."""
        custom_id = "custom-signal-id-12345"
        signal = _make_signal(signal_id=custom_id)
        assert signal.signal_id == custom_id

    def test_empty_signal_id_rejected(self) -> None:
        """Empty string signal_id raises ValidationError."""
        with pytest.raises(ValidationError, match="signal_id must be"):
            _make_signal(signal_id="")

    def test_whitespace_signal_id_rejected(self) -> None:
        """Whitespace-only signal_id raises ValidationError."""
        with pytest.raises(ValidationError, match="signal_id must be"):
            _make_signal(signal_id="   ")


class TestSignalIdRoundTrip:
    """signal_id round-trips through msgspec encode/decode."""

    def test_signal_id_roundtrips_via_msgspec(self) -> None:
        """Encode with msgspec, decode, and verify signal_id."""
        signal = _make_signal()
        original_id = signal.signal_id
        from atlas.shared.serialisation import pydantic_to_msgspec
        encoded = pydantic_to_msgspec(signal)
        decoded: dict = decode_json(encoded)  # type: ignore[type-arg]

        assert decoded["signal_id"] == original_id


class TestCategoryScoresValidation:
    """CategoryScores.total must match the sum of categories."""

    def test_mismatched_total_rejected(self) -> None:
        """total != sum raises ValidationError."""
        with pytest.raises(ValidationError, match="does not match sum"):
            CategoryScores(
                derivatives=10,
                onchain=10,
                technical=10,
                sentiment=10,
                context=10,
                total=99,
            )

    def test_correct_total_accepted(self) -> None:
        """total == sum is valid."""
        scores = CategoryScores(
            derivatives=10,
            onchain=10,
            technical=10,
            sentiment=10,
            context=10,
            total=50,
        )
        assert scores.total == 50


class TestFrozenModels:
    """All models must be immutable (frozen=True)."""

    def test_signal_output_is_frozen(self) -> None:
        """SignalOutput attributes cannot be reassigned."""
        signal = _make_signal()
        with pytest.raises(ValidationError):
            signal.score = 50  # type: ignore[misc]

    def test_action_block_is_frozen(self) -> None:
        """ActionBlock attributes cannot be reassigned."""
        action = _make_action_block()
        with pytest.raises(ValidationError):
            action.side = "sell"  # type: ignore[misc]

    def test_category_scores_is_frozen(self) -> None:
        """CategoryScores attributes cannot be reassigned."""
        scores = _make_category_scores()
        with pytest.raises(ValidationError):
            scores.total = 999  # type: ignore[misc]


class TestSchemaVersion:
    """schema_version field on SignalOutput (Audit S1.3)."""

    def test_default_schema_version(self) -> None:
        """Default schema_version matches SIGNAL_SCHEMA_VERSION."""
        from atlas.models.signal import SIGNAL_SCHEMA_VERSION
        signal = _make_signal()
        assert signal.schema_version == SIGNAL_SCHEMA_VERSION

    def test_schema_version_present_in_model_fields(self) -> None:
        """schema_version is declared on SignalOutput."""
        assert "schema_version" in SignalOutput.model_fields

    def test_schema_version_roundtrips(self) -> None:
        """schema_version survives msgspec encode/decode."""
        signal = _make_signal()
        from atlas.shared.serialisation import pydantic_to_msgspec, decode_json
        encoded = pydantic_to_msgspec(signal)
        decoded: dict = decode_json(encoded)  # type: ignore[type-arg]
        assert decoded["schema_version"] == signal.schema_version

    def test_custom_schema_version_preserved(self) -> None:
        """Explicitly set schema_version is preserved."""
        signal = _make_signal(schema_version="99.0.0")
        assert signal.schema_version == "99.0.0"



