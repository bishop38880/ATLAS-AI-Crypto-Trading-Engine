"""Outcome classification logic for Post-Trade Learning.

Maps raw PnL and exit reasons against original signal conviction
to categorize the learning value of the trade.
"""

from __future__ import annotations

from atlas.signals.outcome import ExitReason, MarketOutcome, OutcomeClassification, TradeOutcome


def classify_outcome(
    outcome: TradeOutcome,
    raw_confluence_score: int,
) -> OutcomeClassification:
    """Classify a trade outcome based on conviction and PnL.

    Rules:
        - High conviction (score >= 140) + WIN = GOOD_WIN
        - High conviction + LOSS = BAD_LOSS
        - Low conviction (score < 140) + WIN = LUCKY_WIN
        - Low conviction + LOSS = GOOD_LOSS

    Args:
        outcome: The TradeOutcome payload from PROMETHEUS.
        raw_confluence_score: The raw 220-point score from ATLAS.

    Returns:
        The computed OutcomeClassification.
    """
    is_high_conviction = raw_confluence_score >= 140
    market_outcome = outcome.market_outcome

    if is_high_conviction:
        if market_outcome == MarketOutcome.WIN:
            return OutcomeClassification.GOOD_WIN
        return OutcomeClassification.BAD_LOSS

    # Low conviction
    if market_outcome == MarketOutcome.WIN:
        return OutcomeClassification.LUCKY_WIN
    return OutcomeClassification.GOOD_LOSS
