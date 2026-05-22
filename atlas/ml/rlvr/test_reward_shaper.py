"""Tests for RLVR reward shaper."""

from decimal import Decimal

from atlas.ml.rlvr.action_decoder import RLVRAction
from atlas.ml.rlvr.reward_shaper import RewardShaper
from atlas.models.signal import TradeOutcome, MarketOutcome, ExitReason


def test_reward_shaping() -> None:
    """Verify reward shaping rules."""
    shaper = RewardShaper(loss_penalty_factor=1.5, abstention_bonus=0.01)

    # 1. Winning trade
    action_long = RLVRAction(trade_decision="LONG", trade_confidence=0.8, weight_adjustments=[0.0]*5)
    outcome_win = TradeOutcome(signal_id="123", pnl_pct=Decimal("1.0"), exit_reason=ExitReason.TAKE_PROFIT)
    reward = shaper.compute_reward(action_long, outcome_win, base_would_have_entered=True)
    assert reward > 0.0

    # 2. Losing trade (asymmetric penalty)
    outcome_loss = TradeOutcome(signal_id="123", pnl_pct=Decimal("-1.0"), exit_reason=ExitReason.STOP_LOSS)
    reward_loss = shaper.compute_reward(action_long, outcome_loss, base_would_have_entered=True)
    assert reward_loss < -1.0  # Should be heavily penalized

    # 3. Abstention on losing trade
    action_abstain = RLVRAction(trade_decision="ABSTAIN", trade_confidence=0.5, weight_adjustments=[0.0]*5)
    reward_abstain = shaper.compute_reward(action_abstain, outcome_loss, base_would_have_entered=True)
    assert reward_abstain == 0.01  # Abstention bonus
