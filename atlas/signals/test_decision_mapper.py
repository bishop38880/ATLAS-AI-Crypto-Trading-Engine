"""Tests for decision_mapper — Session 00 quality gate.

Tests live alongside code (atlas/signals/test_decision_mapper.py).
"""

from __future__ import annotations

import pytest

from atlas.models.signal import AgentResult, SignalDirection, SignalDecision
from atlas.signals.decision_mapper import (
    calculate_decision,
    determine_direction,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(
    name: str,
    direction: SignalDirection,
    score: int = 50,
    max_score: int = 100,
) -> AgentResult:
    """Build an AgentResult with the given direction.

    Args:
        name: Agent name.
        direction: Directional conviction.
        score: Agent score.
        max_score: Maximum possible score.

    Returns:
        A valid AgentResult instance.
    """
    return AgentResult(
        agent_name=name,
        score=score,
        max_score=max_score,
        direction=direction,
    )


# ---------------------------------------------------------------------------
# Tests — calculate_decision
# ---------------------------------------------------------------------------


class TestCalculateDecision:
    """Tests for score → decision mapping."""

    def test_strong_buy_on_high_score_bullish(self) -> None:
        """Score 85 + bullish consensus → Strong Buy."""
        decision = calculate_decision(
            score=85,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.STRONG_BUY

    def test_strong_sell_on_high_score_bearish(self) -> None:
        """Score 85 + bearish consensus → Strong Sell."""
        decision = calculate_decision(
            score=85,
            direction=SignalDirection.BEARISH,
        )
        assert decision == SignalDecision.STRONG_SELL

    def test_buy_on_moderate_score_bullish(self) -> None:
        """Score 65 + bullish → Buy (not Strong Buy)."""
        decision = calculate_decision(
            score=65,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.BUY

    def test_sell_on_moderate_score_bearish(self) -> None:
        """Score 65 + bearish → Sell (not Strong Sell)."""
        decision = calculate_decision(
            score=65,
            direction=SignalDirection.BEARISH,
        )
        assert decision == SignalDecision.SELL

    def test_risk_veto_overrides_high_score(self) -> None:
        """risk_veto=True + score 95 → No Position."""
        decision = calculate_decision(
            score=95,
            direction=SignalDirection.BULLISH,
            risk_veto=True,
        )
        assert decision == SignalDecision.NO_POSITION

    def test_low_score_no_position(self) -> None:
        """Score 35 → No Position regardless of direction."""
        decision = calculate_decision(
            score=35,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.NO_POSITION

    def test_hold_on_mid_score(self) -> None:
        """Score 45 → Hold."""
        decision = calculate_decision(
            score=45,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.HOLD

    def test_neutral_direction_at_high_score_is_hold(self) -> None:
        """Score 90 + neutral direction → Hold (no directional trade)."""
        decision = calculate_decision(
            score=90,
            direction=SignalDirection.NEUTRAL,
        )
        assert decision == SignalDecision.HOLD

    def test_boundary_score_40_is_hold(self) -> None:
        """Score exactly 40 → Hold (boundary)."""
        decision = calculate_decision(
            score=40,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.HOLD

    def test_boundary_score_60_is_buy(self) -> None:
        """Score exactly 60 + bullish → Buy (boundary)."""
        decision = calculate_decision(
            score=60,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.BUY

    def test_boundary_score_80_is_strong_buy(self) -> None:
        """Score exactly 80 + bullish → Strong Buy (boundary)."""
        decision = calculate_decision(
            score=80,
            direction=SignalDirection.BULLISH,
        )
        assert decision == SignalDecision.STRONG_BUY


# ---------------------------------------------------------------------------
# Tests — determine_direction
# ---------------------------------------------------------------------------


class TestDetermineDirection:
    """Tests for agent direction aggregation."""

    def test_majority_bullish(self) -> None:
        """3 bullish + 1 bearish → bullish consensus."""
        results = [
            _make_agent("a", SignalDirection.BULLISH),
            _make_agent("b", SignalDirection.BULLISH),
            _make_agent("c", SignalDirection.BULLISH),
            _make_agent("d", SignalDirection.BEARISH),
        ]
        assert determine_direction(results) == SignalDirection.BULLISH

    def test_majority_bearish(self) -> None:
        """3 bearish + 1 bullish → bearish consensus."""
        results = [
            _make_agent("a", SignalDirection.BEARISH),
            _make_agent("b", SignalDirection.BEARISH),
            _make_agent("c", SignalDirection.BEARISH),
            _make_agent("d", SignalDirection.BULLISH),
        ]
        assert determine_direction(results) == SignalDirection.BEARISH

    def test_tie_resolves_to_neutral(self) -> None:
        """2 bullish + 2 bearish → neutral (tie)."""
        results = [
            _make_agent("a", SignalDirection.BULLISH),
            _make_agent("b", SignalDirection.BEARISH),
            _make_agent("c", SignalDirection.BULLISH),
            _make_agent("d", SignalDirection.BEARISH),
        ]
        assert determine_direction(results) == SignalDirection.NEUTRAL

    def test_all_neutral_is_neutral(self) -> None:
        """All neutral agents → neutral."""
        results = [
            _make_agent("a", SignalDirection.NEUTRAL),
            _make_agent("b", SignalDirection.NEUTRAL),
        ]
        assert determine_direction(results) == SignalDirection.NEUTRAL

    def test_empty_results_is_neutral(self) -> None:
        """Empty agent list → neutral."""
        assert determine_direction([]) == SignalDirection.NEUTRAL

    def test_neutral_agents_abstain(self) -> None:
        """Neutral agents don't count — 1 bullish + 2 neutral → bullish."""
        results = [
            _make_agent("a", SignalDirection.BULLISH),
            _make_agent("b", SignalDirection.NEUTRAL),
            _make_agent("c", SignalDirection.NEUTRAL),
        ]
        assert determine_direction(results) == SignalDirection.BULLISH
