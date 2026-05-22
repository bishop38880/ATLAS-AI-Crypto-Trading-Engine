from decimal import Decimal

from atlas.signals.outcome import ExitReason, OutcomeClassification, TradeOutcome
from atlas.signals.lesson_generator import generate_lesson


def test_generate_lesson_good_win() -> None:
    outcome = TradeOutcome(signal_id="sig-1", pnl_pct=Decimal("2.5"), exit_reason=ExitReason.TAKE_PROFIT)
    res = generate_lesson(
        outcome, OutcomeClassification.GOOD_WIN, 150, "Buy", "Strong fundamentals"
    )
    assert "Expected Win" in res
    assert "+2.50%" in res
    assert "TAKE_PROFIT" in res
    assert "150/220" in res
    assert "Strong fundamentals" in res


def test_generate_lesson_lucky_win() -> None:
    outcome = TradeOutcome(signal_id="sig-2", pnl_pct=Decimal("1.0"), exit_reason=ExitReason.TIME_EXPIRY)
    res = generate_lesson(
        outcome, OutcomeClassification.LUCKY_WIN, 100, "Buy", "Weak signals"
    )
    assert "Lucky Win" in res
    assert "TIME_EXPIRY" in res
    assert "market forces likely bailed us out" in res


def test_generate_lesson_good_loss() -> None:
    outcome = TradeOutcome(signal_id="sig-3", pnl_pct=Decimal("-0.5"), exit_reason=ExitReason.STOP_LOSS)
    res = generate_lesson(
        outcome, OutcomeClassification.GOOD_LOSS, 90, "Sell", "Maybe short"
    )
    assert "Controlled Loss" in res
    assert "-0.50%" in res
    assert "invalid, but loss was managed appropriately" in res


def test_generate_lesson_bad_loss() -> None:
    outcome = TradeOutcome(signal_id="sig-4", pnl_pct=Decimal("-3.0"), exit_reason=ExitReason.LIQUIDATION)
    res = generate_lesson(
        outcome, OutcomeClassification.BAD_LOSS, 180, "Strong Buy", "Huge confluence"
    )
    assert "Model Failure" in res
    assert "failed. Needs review to identify missing risk factors" in res
