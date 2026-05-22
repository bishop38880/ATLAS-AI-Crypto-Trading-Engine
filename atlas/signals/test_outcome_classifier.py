from decimal import Decimal

from atlas.signals.outcome import ExitReason, OutcomeClassification, TradeOutcome
from atlas.signals.outcome_classifier import classify_outcome


def test_classify_good_win() -> None:
    outcome = TradeOutcome(
        signal_id="sig-1",
        pnl_pct=Decimal("1.5"),
        exit_reason=ExitReason.TAKE_PROFIT,
    )
    # Conviction >= 140
    res = classify_outcome(outcome, 150)
    assert res == OutcomeClassification.GOOD_WIN


def test_classify_bad_loss() -> None:
    outcome = TradeOutcome(
        signal_id="sig-2",
        pnl_pct=Decimal("-2.0"),
        exit_reason=ExitReason.STOP_LOSS,
    )
    res = classify_outcome(outcome, 150)
    assert res == OutcomeClassification.BAD_LOSS


def test_classify_lucky_win() -> None:
    outcome = TradeOutcome(
        signal_id="sig-3",
        pnl_pct=Decimal("1.0"),
        exit_reason=ExitReason.TAKE_PROFIT,
    )
    # Conviction < 140
    res = classify_outcome(outcome, 100)
    assert res == OutcomeClassification.LUCKY_WIN


def test_classify_good_loss() -> None:
    outcome = TradeOutcome(
        signal_id="sig-4",
        pnl_pct=Decimal("-0.6"),
        exit_reason=ExitReason.TRAILING_STOP,
    )
    res = classify_outcome(outcome, 100)
    assert res == OutcomeClassification.GOOD_LOSS


def test_classify_scratch_low_conviction() -> None:
    outcome = TradeOutcome(
        signal_id="sig-5",
        pnl_pct=Decimal("0.1"),
        exit_reason=ExitReason.TIME_EXPIRY,
    )
    # A scratch is not a win, so it falls to LOSS branch in classifier
    res = classify_outcome(outcome, 100)
    assert res == OutcomeClassification.GOOD_LOSS


def test_classify_scratch_high_conviction() -> None:
    outcome = TradeOutcome(
        signal_id="sig-6",
        pnl_pct=Decimal("0.1"),
        exit_reason=ExitReason.TIME_EXPIRY,
    )
    # A scratch is not a win, so it falls to BAD_LOSS branch in classifier
    res = classify_outcome(outcome, 150)
    assert res == OutcomeClassification.BAD_LOSS
