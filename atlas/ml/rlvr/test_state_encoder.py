"""Tests for RLVR state encoder."""

import pytest

from atlas.ml.rlvr.state_encoder import RLVRState, STATE_DIM


def test_state_encoder_dimensions() -> None:
    """Verify that RLVRState produces exactly a 42-dimensional vector."""
    state = RLVRState(
        agent_scores=[0.5] * 5,
        agent_confidences=[0.8] * 5,
        agent_directions=[1.0, -1.0, 0.0, 1.0, -1.0],
        regime_bull_prob=0.6,
        regime_bear_prob=0.2,
        regime_volatile_prob=0.2,
        regime_duration_normalized=0.1,
        regime_transition_prob=0.05,
        volatility_zscore=1.2,
        portfolio_exposure=0.5,
        position_count_normalized=0.3,
        portfolio_direction=1.0,
        portfolio_correlation=0.4,
        consecutive_losses_normalized=0.0,
        drawdown_fraction=0.02,
        base_conviction=0.7,
        meta_learner_score=0.75,
        thompson_entropy=0.3,
        hard_point_fraction=0.8,
        macro_suppression=0.0,
        hour_sin=0.5,
        hour_cos=0.5,
        dow_sin=0.5,
        dow_cos=0.5,
        recent_win_rate=0.6,
        recent_avg_pnl=0.01,
        recent_sharpe=1.5,
        recent_good_win_rate=0.4,
        recent_bad_loss_rate=0.05,
        signal_frequency=0.2,
    )

    vector = state.to_vector()
    assert len(vector) == STATE_DIM
    assert len(vector) == 42


def test_state_frozen_invariant() -> None:
    """Verify that RLVRState is frozen."""
    state = RLVRState(
        agent_scores=[0.5] * 5,
        agent_confidences=[0.8] * 5,
        agent_directions=[1.0, -1.0, 0.0, 1.0, -1.0],
        regime_bull_prob=0.6,
        regime_bear_prob=0.2,
        regime_volatile_prob=0.2,
        regime_duration_normalized=0.1,
        regime_transition_prob=0.05,
        volatility_zscore=1.2,
        portfolio_exposure=0.5,
        position_count_normalized=0.3,
        portfolio_direction=1.0,
        portfolio_correlation=0.4,
        consecutive_losses_normalized=0.0,
        drawdown_fraction=0.02,
        base_conviction=0.7,
        meta_learner_score=0.75,
        thompson_entropy=0.3,
        hard_point_fraction=0.8,
        macro_suppression=0.0,
        hour_sin=0.5,
        hour_cos=0.5,
        dow_sin=0.5,
        dow_cos=0.5,
        recent_win_rate=0.6,
        recent_avg_pnl=0.01,
        recent_sharpe=1.5,
        recent_good_win_rate=0.4,
        recent_bad_loss_rate=0.05,
        signal_frequency=0.2,
    )

    with pytest.raises(Exception):
        state.regime_bull_prob = 0.9  # type: ignore
