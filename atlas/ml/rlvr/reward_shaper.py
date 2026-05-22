"""Reward shaper for RLVR."""

from __future__ import annotations

from decimal import Decimal

from atlas.ml.rlvr.action_decoder import RLVRAction
from atlas.models.signal import TradeOutcome, MarketOutcome


class RewardShaper:
    """Calculates reinforcement reward for an RLVR action based on P&L.

    Formula:
    reward = shaped_pnl + risk_penalty + calibration_bonus + abstention_reward
    """

    def __init__(self, loss_penalty_factor: float = 1.5, abstention_bonus: float = 0.01) -> None:
        """Initialize with shaping hyperparameters."""
        self._loss_factor = loss_penalty_factor
        self._abstention_bonus = abstention_bonus

    def compute_reward(self, action: RLVRAction, outcome: TradeOutcome, base_would_have_entered: bool) -> float:
        """Compute the total shaped reward."""
        pnl = outcome.pnl_pct
        loss_factor = Decimal(str(self._loss_factor))

        # 1. Base P&L contribution
        if pnl < Decimal("0"):
            shaped_pnl = float(pnl * loss_factor)  # dimensionless ratio — float OK per Invariant 8
        else:
            shaped_pnl = float(pnl)  # dimensionless ratio — float OK per Invariant 8

        # 2. Abstention Reward
        abstention_reward = 0.0
        if action.trade_decision == "ABSTAIN":
            if base_would_have_entered and outcome.market_outcome == MarketOutcome.LOSS:
                abstention_reward = self._abstention_bonus
            shaped_pnl = 0.0  # If we abstained, we didn't experience the P&L directly

        # 3. Direction mismatch penalty
        if action.trade_decision == "LONG" and outcome.market_outcome == MarketOutcome.LOSS:
            pass  # Already penalized via shaped_pnl
        elif action.trade_decision == "SHORT" and outcome.market_outcome == MarketOutcome.WIN:
            # Reversing a winning trade
            shaped_pnl = -abs(shaped_pnl) * self._loss_factor

        # 4. Calibration Bonus
        # Higher confidence on wins -> more reward. Lower confidence on losses -> less penalty.
        calibration_bonus = 0.0
        if action.trade_decision != "ABSTAIN":
            if shaped_pnl > 0:
                calibration_bonus = (action.trade_confidence - 0.5) * 0.05
            elif shaped_pnl < 0:
                calibration_bonus = (0.5 - action.trade_confidence) * 0.05

        return shaped_pnl + abstention_reward + calibration_bonus
