"""State encoder for RLVR. Maps ATLAS context to a 42-dimensional vector."""

from __future__ import annotations

from pydantic import BaseModel, Field


STATE_DIM = 42


class RLVRState(BaseModel, frozen=True):
    """Immutable state representation for RLVR policy network.

    Maps to 5 Tier-1 Analysts:
    Technical, Derivatives, OnChain, Sentiment, MarketRegime
    """

    # === AGENT VERDICTS (15 dims) ===
    agent_scores: list[float] = Field(min_length=5, max_length=5)
    agent_confidences: list[float] = Field(min_length=5, max_length=5)
    agent_directions: list[float] = Field(min_length=5, max_length=5)

    # === MARKET REGIME (6 dims) ===
    regime_bull_prob: float
    regime_bear_prob: float
    regime_volatile_prob: float
    regime_duration_normalized: float
    regime_transition_prob: float
    volatility_zscore: float

    # === PORTFOLIO CONTEXT (6 dims) ===
    portfolio_exposure: float
    position_count_normalized: float
    portfolio_direction: float
    portfolio_correlation: float
    consecutive_losses_normalized: float
    drawdown_fraction: float

    # === SCORING CONTEXT (5 dims) ===
    base_conviction: float
    meta_learner_score: float
    thompson_entropy: float
    hard_point_fraction: float
    macro_suppression: float

    # === TEMPORAL (4 dims) ===
    hour_sin: float
    hour_cos: float
    dow_sin: float
    dow_cos: float

    # === RECENT PERFORMANCE (6 dims) ===
    recent_win_rate: float
    recent_avg_pnl: float
    recent_sharpe: float
    recent_good_win_rate: float
    recent_bad_loss_rate: float
    signal_frequency: float

    def to_vector(self) -> list[float]:
        """Convert state to a flat list of exactly STATE_DIM length."""
        return [
            *self.agent_scores,
            *self.agent_confidences,
            *self.agent_directions,
            self.regime_bull_prob, self.regime_bear_prob,
            self.regime_volatile_prob, self.regime_duration_normalized,
            self.regime_transition_prob, self.volatility_zscore,
            self.portfolio_exposure, self.position_count_normalized,
            self.portfolio_direction, self.portfolio_correlation,
            self.consecutive_losses_normalized, self.drawdown_fraction,
            self.base_conviction, self.meta_learner_score,
            self.thompson_entropy, self.hard_point_fraction,
            self.macro_suppression,
            self.hour_sin, self.hour_cos, self.dow_sin, self.dow_cos,
            self.recent_win_rate, self.recent_avg_pnl,
            self.recent_sharpe, self.recent_good_win_rate,
            self.recent_bad_loss_rate, self.signal_frequency,
        ]
