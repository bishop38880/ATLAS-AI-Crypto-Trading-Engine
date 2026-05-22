from decimal import Decimal

import pytest
from pydantic import ValidationError

from atlas.signals.outcome import ExitReason, MarketOutcome, TradeOutcome


def test_trade_outcome_win() -> None:
    outcome = TradeOutcome(
        signal_id="sig-123",
        pnl_pct=Decimal("2.5"),
        exit_reason=ExitReason.TAKE_PROFIT,
    )
    assert outcome.market_outcome == MarketOutcome.WIN


def test_trade_outcome_loss() -> None:
    outcome = TradeOutcome(
        signal_id="sig-124",
        pnl_pct=Decimal("-1.5"),
        exit_reason=ExitReason.STOP_LOSS,
    )
    assert outcome.market_outcome == MarketOutcome.LOSS


def test_trade_outcome_scratch() -> None:
    outcome = TradeOutcome(
        signal_id="sig-125",
        pnl_pct=Decimal("0.2"),
        exit_reason=ExitReason.TIME_EXPIRY,
    )
    assert outcome.market_outcome == MarketOutcome.SCRATCH


def test_trade_outcome_invalid() -> None:
    with pytest.raises(ValidationError):
        TradeOutcome(
            signal_id="",
            pnl_pct=Decimal("1.0"),
            exit_reason=ExitReason.TAKE_PROFIT,
        )
