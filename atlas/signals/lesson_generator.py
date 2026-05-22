"""Auto-lesson generation for post-trade feedback loop.

Synthesizes deterministic RAG-friendly summaries explaining trade outcomes.
"""

from __future__ import annotations

from atlas.models.signal import SignalOutput
from atlas.signals.outcome import OutcomeClassification, TradeOutcome


def generate_lesson(
    outcome: TradeOutcome,
    classification: OutcomeClassification,
    raw_score: int,
    decision: str,
    reasoning: str,
) -> str:
    """Generate a deterministic 200-word RAG-friendly lesson.

    Args:
        outcome: The TradeOutcome payload.
        classification: The contextual category of the outcome.
        raw_score: The original raw confluence score (0-220).
        decision: The original decision (e.g., 'Buy', 'Sell').
        reasoning: The original reasoning summary.

    Returns:
        A concise text summary to be embedded and stored in pattern_memory.
    """
    generators = {
        OutcomeClassification.GOOD_WIN: _good_win_lesson,
        OutcomeClassification.LUCKY_WIN: _lucky_win_lesson,
        OutcomeClassification.GOOD_LOSS: _good_loss_lesson,
        OutcomeClassification.BAD_LOSS: _bad_loss_lesson,
    }
    gen = generators[classification]
    return gen(outcome.pnl_pct, outcome.exit_reason.value, raw_score, decision, reasoning)


def _good_win_lesson(
    pnl: object, reason: str, conviction: int, decision: str, reasoning: str,
) -> str:
    """Generate lesson for expected wins."""
    return (
        f"Expected Win: Trade closed with {pnl:+.2f}% PnL ({reason}). "
        f"High conviction ({conviction}/220) {decision} signal played out as anticipated. "
        f"Original reasoning: '{reasoning}'."
    )


def _lucky_win_lesson(
    pnl: object, reason: str, conviction: int, decision: str, reasoning: str,
) -> str:
    """Generate lesson for lucky wins."""
    return (
        f"Lucky Win: Trade closed with {pnl:+.2f}% PnL ({reason}). "
        f"Low conviction ({conviction}/220) {decision} signal was profitable, "
        f"but market forces likely bailed us out. Review for false positives. "
        f"Original reasoning: '{reasoning}'."
    )


def _good_loss_lesson(
    pnl: object, reason: str, conviction: int, decision: str, reasoning: str,
) -> str:
    """Generate lesson for controlled losses."""
    return (
        f"Controlled Loss: Trade closed with {pnl:+.2f}% PnL ({reason}). "
        f"Low conviction ({conviction}/220) {decision} signal was invalid, "
        f"but loss was managed appropriately. "
        f"Original reasoning: '{reasoning}'."
    )


def _bad_loss_lesson(
    pnl: object, reason: str, conviction: int, decision: str, reasoning: str,
) -> str:
    """Generate lesson for model failures."""
    return (
        f"Model Failure: Trade closed with {pnl:+.2f}% PnL ({reason}). "
        f"High conviction ({conviction}/220) {decision} signal failed. "
        f"Needs review to identify missing risk factors or false convergences. "
        f"Original reasoning: '{reasoning}'."
    )
