"""Decision mapper — maps normalised scores to SignalDecision.

Pure logic module with zero side effects. The ConfluenceScorer
calls these functions after aggregating agent results.

Direction (buy vs sell) is determined by agent consensus.
Score magnitude determines signal strength (Strong/regular/Hold/No).

Thresholds (Session 00 defaults — subject to tuning):
    - risk_veto=True → No Position (always, regardless of score)
    - score >= 80 → Strong Buy or Strong Sell
    - score >= 60 → Buy or Sell
    - score >= 40 → Hold
    - score < 40  → No Position
"""

from __future__ import annotations

from atlas.models.signal import AgentResult, SignalDirection, CategoryScores, SignalDecision


# ---------------------------------------------------------------------------
# Score → decision thresholds
# ---------------------------------------------------------------------------

_STRONG_THRESHOLD: int = 80
_MODERATE_THRESHOLD: int = 60
_HOLD_THRESHOLD: int = 40


def calculate_decision(
    score: int,
    direction: SignalDirection,
    risk_veto: bool = False,
) -> SignalDecision:
    """Map normalised score + direction to a SignalDecision.

    Args:
        score: Normalised confluence score (0–100).
        direction: Consensus direction from agent aggregation.
        risk_veto: If True, forces No Position regardless of score.

    Returns:
        One of the six permitted SignalDecision values.
    """
    if risk_veto:
        return SignalDecision.NO_POSITION

    if score < _HOLD_THRESHOLD:
        return SignalDecision.NO_POSITION

    if score < _MODERATE_THRESHOLD:
        return SignalDecision.HOLD

    return _map_directional_decision(score, direction)


def _map_directional_decision(
    score: int,
    direction: SignalDirection,
) -> SignalDecision:
    """Map score ≥ 60 to a directional decision.

    If direction is NEUTRAL, defaults to HOLD — no trade without
    directional conviction.

    Args:
        score: Normalised score (already verified ≥ 60).
        direction: Agent consensus direction.

    Returns:
        A directional SignalDecision or HOLD if neutral.
    """
    if direction == SignalDirection.NEUTRAL:
        return SignalDecision.HOLD

    is_strong = score >= _STRONG_THRESHOLD
    if direction == SignalDirection.BULLISH:
        return (
            SignalDecision.STRONG_BUY if is_strong
            else SignalDecision.BUY
        )
    return (
        SignalDecision.STRONG_SELL if is_strong
        else SignalDecision.SELL
    )


def determine_direction(
    agent_results: list[AgentResult],
) -> SignalDirection:
    """Aggregate agent directions into a consensus direction.

    Counts BULLISH vs BEARISH votes. NEUTRAL agents abstain.
    Ties resolve to NEUTRAL (no conviction = no directional trade).

    Args:
        agent_results: List of agent results to aggregate.

    Returns:
        Consensus SignalDirection.
    """
    bullish_count = _count_direction(
        agent_results, SignalDirection.BULLISH,
    )
    bearish_count = _count_direction(
        agent_results, SignalDirection.BEARISH,
    )

    if bullish_count > bearish_count:
        return SignalDirection.BULLISH
    if bearish_count > bullish_count:
        return SignalDirection.BEARISH
    return SignalDirection.NEUTRAL


def _count_direction(
    agent_results: list[AgentResult],
    target: SignalDirection,
) -> int:
    """Count how many agents voted for a specific direction.

    Args:
        agent_results: List of agent results.
        target: Direction to count.

    Returns:
        Number of agents with this direction.
    """
    return sum(
        1 for r in agent_results if r.direction == target
    )
