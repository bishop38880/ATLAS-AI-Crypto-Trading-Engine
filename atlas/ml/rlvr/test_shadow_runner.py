"""Tests for RLVR shadow runner."""

import pytest

from atlas.ml.rlvr.policy_network import SignalPolicyNetwork
from atlas.ml.rlvr.shadow_runner import ShadowRunner
from atlas.ml.rlvr.state_encoder import RLVRState


@pytest.fixture
def dummy_state() -> RLVRState:
    return RLVRState(
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


@pytest.mark.asyncio
async def test_risk_veto_enforced(dummy_state: RLVRState) -> None:
    """Verify risk veto overrides everything."""
    policy = SignalPolicyNetwork()
    runner = ShadowRunner(policy, "dummy_url") # type: ignore
    
    decision, weights = await runner.execute(
        state=dummy_state,
        base_decision="BUY",
        risk_veto=True,
        rlvr_live=True,
        signal_id="123"
    )
    
    assert decision == "NO_POSITION"
    assert weights is None


@pytest.mark.asyncio
async def test_shadow_mode(dummy_state: RLVRState) -> None:
    """Verify rlvr_live=False returns base decision."""
    policy = SignalPolicyNetwork()
    runner = ShadowRunner(policy, "dummy_url") # type: ignore
    
    # We monkeypatch the log_to_postgres so it doesn't crash on dummy_url
    async def mock_log(*args, **kwargs):
        pass
    runner._log_to_postgres = mock_log  # type: ignore
    
    decision, weights = await runner.execute(
        state=dummy_state,
        base_decision="SELL",
        risk_veto=False,
        rlvr_live=False,
        signal_id="123"
    )
    
    assert decision == "SELL"
    assert weights is None
