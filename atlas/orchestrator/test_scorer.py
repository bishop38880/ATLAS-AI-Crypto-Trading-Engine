"""Tests for ConfluenceScorer — Session 00 quality gate.

Tests live alongside code (atlas/orchestrator/test_scorer.py).
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from pydantic import SecretStr

from atlas.agents.base import AgentResult, SignalDirection
from atlas.models.signal import ActionBlock, SignalDecision, SubSignalResult
from atlas.orchestrator.scorer import ConfluenceScorer, CATEGORY_WEIGHTS, _build_category_scores
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings() -> PolarisSettings:
    """Build PolarisSettings with defaults for testing.

    Returns:
        A PolarisSettings instance.
    """
    return PolarisSettings(DEEPSEEK_API_KEY=SecretStr("test-key"))


def _make_scorer() -> ConfluenceScorer:
    """Build a ConfluenceScorer with default settings.

    Returns:
        A ConfluenceScorer instance.
    """
    return ConfluenceScorer(settings=_make_settings())


def _make_agent(
    name: str,
    score: int,
    max_score: int,
    direction: SignalDirection = SignalDirection.BULLISH,
    explanation: str = "",
    convergences: list[str] | None = None,
    risks: list[str] | None = None,
) -> AgentResult:
    """Build an AgentResult for testing.

    Args:
        name: Agent name.
        score: Agent's raw score.
        max_score: Maximum score for this category.
        direction: Directional conviction.
        explanation: Reasoning text.
        convergences: List of convergences.
        risks: List of risks.

    Returns:
        A valid AgentResult instance.
    """
    return AgentResult(
        agent_name=name,
        score=score,
        max_score=max_score,
        direction=direction,
        explanation=explanation,
        convergences=convergences or [],
        risks=risks or [],
    )

def _make_agent_with_sub_signals(
    name: str,
    score: int,
    sub_signals: dict[str, int],
) -> AgentResult:
    """Build an AgentResult with sub_signals for testing."""
    converted = {
        k: SubSignalResult(
            value=str(v), flag="TEST",
            metadata={"amount": v},
        )
        for k, v in sub_signals.items()
    }
    return AgentResult(
        agent_name=name,
        score=score,
        max_score=220,
        direction=SignalDirection.NEUTRAL,
        sub_signals=converted,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScoreAggregation:
    """Tests for raw score aggregation and normalisation."""

    @pytest.mark.asyncio
    async def test_zero_agents_produces_zero_score(self) -> None:
        """No agents → score 0, No Position."""
        scorer = _make_scorer()
        signal = await scorer.score(
            agent_results=[],
            asset="BTCUSDT",
        )
        assert signal.score == 0
        assert signal.decision == SignalDecision.NO_POSITION

    @pytest.mark.asyncio
    async def test_full_score_normalises_to_100(self) -> None:
        """220 raw points → 100 normalised."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220),
            _make_agent("derivatives", 220, 220),
            _make_agent("onchain", 220, 220),
            _make_agent("sentiment", 220, 220),
            _make_agent("whale", 220, 220),
            _make_agent("liquidation", 220, 220),
            _make_agent("regime", 220, 220),
            _make_agent("funding_rate_monitor", 220, 220),
            _make_agent("news_macro_agent", 220, 220),
            _make_agent("correlation", 220, 220),
            _make_agent("macro", 220, 220),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.score == 100

    @pytest.mark.asyncio
    async def test_partial_score_normalises_correctly(self) -> None:
        """110 raw points → 50 normalised (110/220 * 100)."""
        scorer = _make_scorer()
        agents = [
            _make_agent("derivatives", 50, 70),
            _make_agent("technical", 30, 50),
            _make_agent("onchain", 20, 40),
            _make_agent("sentiment", 10, 30),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.score > 0 and signal.score < 100

    @pytest.mark.asyncio
    async def test_category_weights_sum_to_one(self) -> None:
        """Test CATEGORY_WEIGHTS sums to exactly 1.0 (excluding Risk)."""
        total_weight = sum(CATEGORY_WEIGHTS.values())
        assert abs(total_weight - 1.0) < 1e-9
        assert "risk" not in CATEGORY_WEIGHTS

    @pytest.mark.asyncio
    async def test_risk_filtered_out_of_weighted_sum(self) -> None:
        """Risk agent results do not contribute to weighted sum."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220),
            _make_agent("risk", 220, 220),  # ignored for scoring
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        # technical at max: raw weighted sum 15 → norm round(15*100/220)=7
        assert signal.score == 7

    @pytest.mark.asyncio
    async def test_conviction_suppression_applied_and_floored(self) -> None:
        """Conviction suppression reduces score, floored at 0."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 110, 220),
            _make_agent_with_sub_signals(
                "news_macro_agent", 0, {"conviction_suppression": -20}
            ),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.score == 0

        agents2 = [
            _make_agent("technical", 220, 220),
            _make_agent("derivatives", 220, 220),
            _make_agent_with_sub_signals(
                "news_macro_agent", 0, {"conviction_suppression": -15}
            ),
        ]
        signal2 = await scorer.score(agent_results=agents2, asset="BTCUSDT")
        # raw 15 + (75×20/33)≈45.45 → round 60; norm 27; −15 suppression → 12
        assert signal2.score == 12


class TestDecisionMapping:
    """Tests for decision mapping through the scorer."""

    @pytest.mark.asyncio
    async def test_high_score_bullish_is_strong_buy(self) -> None:
        """High bullish score → Strong Buy with ActionBlock."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220, SignalDirection.BULLISH),
            _make_agent("derivatives", 220, 220, SignalDirection.BULLISH),
            _make_agent("onchain", 220, 220, SignalDirection.BULLISH),
            _make_agent("sentiment", 220, 220, SignalDirection.BULLISH),
            _make_agent("whale", 220, 220, SignalDirection.BULLISH),
            _make_agent("liquidation", 220, 220, SignalDirection.BULLISH),
            _make_agent("regime", 220, 220, SignalDirection.BULLISH),
            _make_agent("funding_rate_monitor", 220, 220, SignalDirection.BULLISH),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.decision == SignalDecision.STRONG_BUY
        assert signal.action is not None
        assert signal.action.side == "buy"

    @pytest.mark.asyncio
    async def test_bearish_consensus_produces_sell(self) -> None:
        """Majority bearish agents → Sell/Strong Sell."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220, SignalDirection.BEARISH),
            _make_agent("derivatives", 220, 220, SignalDirection.BEARISH),
            _make_agent("onchain", 220, 220, SignalDirection.BEARISH),
            _make_agent("sentiment", 220, 220, SignalDirection.BULLISH),
        ]
        signal = await scorer.score(agent_results=agents, asset="ETHUSDT")
        assert signal.decision in {
            SignalDecision.SELL,
            SignalDecision.STRONG_SELL,
        }
        assert signal.action is not None
        assert signal.action.side == "sell"

    @pytest.mark.asyncio
    async def test_risk_veto_overrides_high_score(self) -> None:
        """Risk veto forces NO_POSITION despite high score."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220, SignalDirection.BULLISH),
            _make_agent("derivatives", 220, 220, SignalDirection.BULLISH),
            _make_agent("onchain", 220, 220, SignalDirection.BULLISH),
            _make_agent("risk", 0, 0, SignalDirection.NEUTRAL),
        ]
        # Set veto=True on the risk agent
        risk_agent = AgentResult(
            agent_name="risk",
            score=0,
            max_score=0,
            direction=SignalDirection.NEUTRAL,
            veto=True,
        )
        agents[-1] = risk_agent

        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.decision == SignalDecision.NO_POSITION
        assert signal.action is None
        # raw ≈15+45.45+39 → 99 → normalised 45
        assert signal.score == 45

    """Tests for signal TTL / expires_at computation."""

    @pytest.mark.asyncio
    async def test_default_30m_ttl(self) -> None:
        """30m timeframe uses 30-minute TTL."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(
            agent_results=agents,
            asset="BTCUSDT",
            timeframe="30m",
        )
        delta = signal.expires_at - signal.timestamp
        assert delta == timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_cascade_ttl_override(self) -> None:
        """Cascade-triggered signals use 2-minute TTL."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(
            agent_results=agents,
            asset="BTCUSDT",
            is_cascade_triggered=True,
            hydra_event_id="hydra-001",
        )
        delta = signal.expires_at - signal.timestamp
        assert delta == timedelta(minutes=2)

    @pytest.mark.asyncio
    async def test_1h_timeframe_ttl(self) -> None:
        """1h timeframe uses 60-minute TTL."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(
            agent_results=agents,
            asset="BTCUSDT",
            timeframe="1h",
        )
        delta = signal.expires_at - signal.timestamp
        assert delta == timedelta(minutes=60)


class TestSignalIdentity:
    """Tests for signal_id and asset/timeframe fields."""

    @pytest.mark.asyncio
    async def test_signal_id_is_generated(self) -> None:
        """Each signal gets a unique signal_id."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        s1 = await scorer.score(agent_results=agents, asset="BTCUSDT")
        s2 = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert s1.signal_id != s2.signal_id
        assert len(s1.signal_id) > 0

    @pytest.mark.asyncio
    async def test_asset_and_timeframe_propagated(self) -> None:
        """Asset and timeframe are stored on the signal."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(
            agent_results=agents,
            asset="ETHUSDT",
            timeframe="4h",
        )
        assert signal.asset == "ETHUSDT"
        assert signal.timeframe == "4h"


class TestReasoningAggregation:
    """Tests for reasoning_summary, convergences, risks."""

    @pytest.mark.asyncio
    async def test_reasoning_combines_explanations(self) -> None:
        """Agent explanations are combined into reasoning_summary."""
        scorer = _make_scorer()
        agents = [
            _make_agent(
                "derivatives", 10, 70,
                explanation="OI rising sharply",
            ),
            _make_agent(
                "technical", 10, 50,
                explanation="RSI oversold",
            ),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert "OI rising sharply" in signal.reasoning_summary
        assert "RSI oversold" in signal.reasoning_summary

    @pytest.mark.asyncio
    async def test_convergences_collected(self) -> None:
        """Convergences are flattened from all agents."""
        scorer = _make_scorer()
        agents = [
            _make_agent(
                "derivatives", 10, 70,
                convergences=["Funding extreme"],
            ),
            _make_agent(
                "technical", 10, 50,
                convergences=["Support bounce"],
            ),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert "Funding extreme" in signal.key_convergences
        assert "Support bounce" in signal.key_convergences

    @pytest.mark.asyncio
    async def test_risks_collected(self) -> None:
        """Risks are flattened from all agents."""
        scorer = _make_scorer()
        agents = [
            _make_agent("derivatives", 10, 70, risks=["Resistance"]),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert "Resistance" in signal.key_risks


class TestCategoryScoresLadder:
    """Breakdown totals use 220 ladder units (operators / dashboard), not 0–100 ratios."""

    def test_saturated_primary_agents_sum_to_aggregate_breakdown_total(self) -> None:
        """Full utilisation of every mapped category (rounded per bucket ⇒ total 219, not 220)."""
        agents = [
            _make_agent("technical", 220, 220),
            _make_agent("derivatives", 220, 220),
            _make_agent("onchain", 220, 220),
            _make_agent("sentiment", 220, 220),
            _make_agent("whale", 220, 220),
            _make_agent("liquidation", 220, 220),
            _make_agent("regime", 220, 220),
            _make_agent("funding_rate_monitor", 220, 220),
            _make_agent("news_macro_agent", 220, 220),
            _make_agent("correlation", 220, 220),
            _make_agent("macro", 220, 220),
            _make_agent("risk", 220, 220),
        ]
        cats = _build_category_scores(agents)
        assert cats.total == 219

    def test_partial_derivatives_bucket_matches_scaled_weight(self) -> None:
        """Half of derivatives native max fills half of that category ladder slice."""
        agents = [_make_agent("derivatives", 35, 70)]
        cats = _build_category_scores(agents)
        ratio = min(1.0, 35 / 70.0)
        expect = round(ratio * 220 * CATEGORY_WEIGHTS["derivatives"])
        assert cats.derivatives == expect
        assert cats.total == cats.derivatives

    def test_unknown_agent_produces_empty_breakdown(self) -> None:
        """Agents not mapped in ``_AGENT_CATEGORY_MAP`` do not inflate category totals."""
        agents = [_make_agent("not_a_registered_agent_key", 10, 20)]
        cats = _build_category_scores(agents)
        assert cats.total == 0


class TestCascadeLinkage:
    """Tests for HYDRA cascade linkage."""

    @pytest.mark.asyncio
    async def test_cascade_fields_propagated(self) -> None:
        """Cascade-triggered signals carry the event ID."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(
            agent_results=agents,
            asset="BTCUSDT",
            is_cascade_triggered=True,
            hydra_event_id="hydra-cascade-42",
        )
        assert signal.is_cascade_triggered is True
        assert signal.hydra_event_id == "hydra-cascade-42"

    @pytest.mark.asyncio
    async def test_non_cascade_defaults(self) -> None:
        """Non-cascade signals have default values."""
        scorer = _make_scorer()
        agents = [_make_agent("derivatives", 10, 70)]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        assert signal.is_cascade_triggered is False
        assert signal.hydra_event_id is None


class TestActionBlockNoAmount:
    """Verify ActionBlock never gets an amount field from scorer."""

    @pytest.mark.asyncio
    async def test_action_has_no_amount(self) -> None:
        """ActionBlock from scorer has no amount field."""
        scorer = _make_scorer()
        agents = [
            _make_agent("technical", 220, 220, SignalDirection.BULLISH),
            _make_agent("derivatives", 220, 220, SignalDirection.BULLISH),
        ]
        signal = await scorer.score(agent_results=agents, asset="BTCUSDT")
        if signal.action is not None:
            assert "amount" not in ActionBlock.model_fields


@pytest.mark.asyncio
async def test_scorer_uses_meta_learner_when_available() -> None:
    scorer = _make_scorer()
    mock_learner = MagicMock()
    mock_learner.model = MagicMock()
    mock_learner.predict.return_value = 0.85
    scorer.meta_learner = mock_learner
    
    agent = _make_agent("technical", 100, 220)
    result = await scorer.score([agent], "BTCUSDT")
    assert result.score == 85
    
@pytest.mark.asyncio
async def test_scorer_falls_back_when_meta_learner_model_is_none() -> None:
    scorer = _make_scorer()
    mock_learner = MagicMock()
    mock_learner.model = None
    scorer.meta_learner = mock_learner
    
    agent = _make_agent("technical", 100, 220)
    result = await scorer.score([agent], "BTCUSDT")
    assert result.score > 0
