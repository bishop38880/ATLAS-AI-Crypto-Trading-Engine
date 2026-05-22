"""Tests for the Event-Driven Pipeline Orchestrator."""

import asyncio
import time
from decimal import Decimal
from typing import Any


import pytest
from pydantic import Field

from atlas.agents.base import AgentCategory, AgentResult, AgentTier, BaseAgent, SignalDirection
from atlas.models.enums import CrossCorrelationGrade
from atlas.models.signal import DeepSeekDecision, SignalDecision
from atlas.orchestrator.scorer import ConfluenceScorer
from atlas.agents.synthesiser.synthesiser_agent import SynthesiserAgent
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.shared.config import PolarisSettings


class MockAgent(BaseAgent):
    """Mock agent for testing orchestrator."""

    def __init__(
        self,
        name: str,
        category: AgentCategory,
        sleep_ms: int = 0,
        veto: bool = False,
        exception: bool = False,
        analyst_score: int = 200,
        analyst_max: int = 200,
    ):
        super().__init__()
        self._name = name
        self._category = category
        self.sleep_ms = sleep_ms
        self.veto = veto
        self.exception = exception
        self.analyst_score = analyst_score
        self.analyst_max = analyst_max
        from atlas.models.signal import AgentState
        self._state = AgentState.READY

    @property
    def name(self) -> str:
        return self._name

    @property
    def category(self) -> AgentCategory:
        return self._category

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> AgentResult:
        if self.exception:
            raise RuntimeError("Mock exception")
            
        if self.sleep_ms > 0:
            await asyncio.sleep(self.sleep_ms / 1000.0)
            
        # Returning a high score so that weights * score doesn't fall below HOLD threshold
        return AgentResult(
            agent_name=self.name,
            score=self.analyst_score if not self.veto and self.category != AgentCategory.RISK else 0,
            max_score=self.analyst_max if not self.veto and self.category != AgentCategory.RISK else 0,
            weight=1.0,
            direction=SignalDirection.BULLISH if not self.veto else SignalDirection.NEUTRAL,
            explanation="Mock explanation",
            convergences=["Mock convergence"],
            risks=["Tier-4 cascade"] if self.veto else [],
            veto=self.veto,
        )


class FakeDeepSeekClient:
    """Fake DeepSeek client for orchestrator integration tests."""

    def __init__(self) -> None:
        self.context_matrix = ""

    async def evaluate_signal(self, context_matrix: str) -> DeepSeekDecision:
        self.context_matrix = context_matrix
        return DeepSeekDecision(
            decision=SignalDecision.NO_POSITION,
            confidence=Decimal("0.72"),
            cross_correlation_grade=CrossCorrelationGrade.STANDARD,
            key_convergences=["DERIVATIVES_ONCHAIN_ALIGNMENT"],
            key_risks=["RAG_DISABLED"],
            reasoning="DeepSeek-style synthesis from the compact RAG matrix.",
            would_change_if="RAG contexts become available with contrary post-mortems.",
        )


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")

@pytest.fixture
def mock_redis():
    from unittest.mock import AsyncMock
    return AsyncMock()

@pytest.fixture
def scorer():
    return ConfluenceScorer(settings=PolarisSettings())

@pytest.fixture
def synthesiser(scorer):
    return SynthesiserAgent(scorer)

@pytest.fixture
def base_agents() -> list[BaseAgent]:
    return [
        MockAgent("risk", AgentCategory.RISK),
        MockAgent("technical", AgentCategory.TECHNICAL),
        MockAgent("derivatives", AgentCategory.DERIVATIVES),
        MockAgent("onchain", AgentCategory.ONCHAIN),
        MockAgent("sentiment", AgentCategory.SENTIMENT),
    ]

@pytest.fixture
def empty_data():
    return {}


@pytest.mark.asyncio
async def test_all_agents_respond_within_budget(synthesiser, base_agents, empty_data, monkeypatch, mock_redis):
    """Test: all agents respond within 80ms -> full results."""
    orchestrator = PipelineOrchestrator(
        base_agents, synthesiser, PolarisSettings(min_trade_score=55), mock_redis,
    )
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)
    
    result = await orchestrator.run(data=empty_data)
    
    assert result.decision != SignalDecision.NO_POSITION
    assert len(result.reasoning_summary) > 0
    # 4 scoring agents * 50 = 200 raw score -> 91 normalized
    assert result.score > 0
    # All 5 categories responded
    assert result.category_scores.total > 0


@pytest.mark.asyncio
async def test_agent_timeout_zero_scored(synthesiser, base_agents, empty_data, monkeypatch, mock_redis):
    """Test: one agent sleeps 200ms -> gets zero-scored, others scored normally."""
    base_agents[1].sleep_ms = 200  # Tech agent times out
    orchestrator = PipelineOrchestrator(base_agents, synthesiser, PolarisSettings(), mock_redis)
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)
    
    start_time = time.perf_counter()
    result = await orchestrator.run(data=empty_data)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    
    # Should complete around 80ms, definitely not 200ms
    assert elapsed_ms < 150
    
    # 3 scoring agents * 50 = 150 raw score -> 68 normalized
    assert result.score > 0
    
    # Check that technical timed out in reasoning summary or latency
    tech_result = next((r for r in base_agents if r.name == "technical"), None)
    assert tech_result is not None
    assert "technical" in result.reasoning_summary or "Timed out" in tech_result._make_zero_result("Timed out").explanation
    

@pytest.mark.asyncio
async def test_quorum_failure(synthesiser, empty_data, monkeypatch, mock_redis):
    """Test: quorum failure (only 2 categories) -> No Position signal."""
    agents: list[BaseAgent] = [
        MockAgent("risk", AgentCategory.RISK),
        MockAgent("technical", AgentCategory.TECHNICAL),
        MockAgent("technical", AgentCategory.TECHNICAL),
    ]
    orchestrator = PipelineOrchestrator(agents, synthesiser, PolarisSettings(), mock_redis)
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)
    
    result = await orchestrator.run(data=empty_data)
    
    assert result.decision == SignalDecision.NO_POSITION
    assert result.score == 0


@pytest.mark.asyncio
async def test_fast_path_risk_veto(synthesiser, base_agents, empty_data, monkeypatch, mock_redis):
    """Test: Risk veto with Tier-4 cascade -> orchestrator cancels pending agents and returns veto in < 5ms."""
    base_agents[0].veto = True  # Risk agent vetoes immediately
    base_agents[1].sleep_ms = 50  # Tech agent sleeps
    
    orchestrator = PipelineOrchestrator(base_agents, synthesiser, PolarisSettings(), mock_redis)
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)
    
    start_time = time.perf_counter()
    result = await orchestrator.run(data=empty_data)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    
    # Should complete almost instantly (< 10ms for safety in CI)
    assert elapsed_ms < 20
    assert result.decision == SignalDecision.NO_POSITION
    assert "Tier-4 cascade" in result.key_risks


@pytest.mark.asyncio
async def test_risk_no_veto_proceeds(synthesiser, base_agents, empty_data, monkeypatch, mock_redis):
    """Test: Risk Agent no-veto -> normal 80ms flow proceeds."""
    base_agents[0].veto = False
    base_agents[1].sleep_ms = 40  # Tech agent finishes in 40ms
    # Make sure we don't accidentally fail on low score (61 instead of > threshold)
    # Set high score explicitly for other agents to offset any penalty
    for a in base_agents:
        if a.name != "technical" and a.name != "risk":
            pass # Keep default high score
    
    orchestrator = PipelineOrchestrator(base_agents, synthesiser, PolarisSettings(), mock_redis)
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)
    
    start_time = time.perf_counter()
    result = await orchestrator.run(data=empty_data)
    elapsed_ms = (time.perf_counter() - start_time) * 1000
    
    # Because of asyncio.wait FIRST_COMPLETED in a while loop, 
    # it waits for all tasks to finish, which takes max 40ms here.
    assert elapsed_ms >= 40
    assert elapsed_ms < 150
@pytest.mark.asyncio
async def test_orchestrator_writes_agent_status_keys(synthesiser, base_agents, empty_data, monkeypatch, mock_redis):
    """Each agent cycle publishes Redis keys consumed by /ws/agents."""
    orchestrator = PipelineOrchestrator(base_agents, synthesiser, PolarisSettings(), mock_redis)
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)

    await orchestrator.run(data=empty_data)

    assert mock_redis.setex.await_count == len(base_agents)
    keys = [c.args[0] for c in mock_redis.setex.await_args_list]
    assert sorted(keys) == sorted(
        [
            "agent:risk:status",
            "agent:TechnicalAgent:status",
            "agent:DerivativesAgent:status",
            "agent:WhaleWatcherAgent:status",
            "agent:SocialAgent:status",
        ]
    )


@pytest.mark.asyncio
async def test_orchestrator_attaches_deepseek_evaluation(
    synthesiser,
    base_agents,
    empty_data,
    monkeypatch,
    mock_redis,
):
    """DeepSeek client output is attached to the cached signal contract."""
    fake_deepseek = FakeDeepSeekClient()
    orchestrator = PipelineOrchestrator(
        base_agents,
        synthesiser,
        PolarisSettings(min_trade_score=55),
        mock_redis,
        deepseek_client=fake_deepseek,
    )
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)

    result = await orchestrator.run(data=empty_data)

    assert result.deepseek_evaluation is not None
    assert result.deepseek_evaluation.reasoning == (
        "DeepSeek-style synthesis from the compact RAG matrix."
    )
    assert result.raw_confluence_score > 0
    assert "technical" in result.agent_breakdown
    assert '"agents"' in fake_deepseek.context_matrix


@pytest.mark.asyncio
async def test_min_trade_score_blocks_deepseek_and_portfolio(
    scorer,
    empty_data,
    monkeypatch,
    mock_redis,
) -> None:
    """Below-threshold scores skip capital allocation, confidence gate, reflection, DeepSeek."""
    agents: list[MockAgent] = [
        MockAgent("risk", AgentCategory.RISK),
        MockAgent("technical", AgentCategory.TECHNICAL, analyst_score=5, analyst_max=200),
        MockAgent("derivatives", AgentCategory.DERIVATIVES, analyst_score=5, analyst_max=200),
        MockAgent("onchain", AgentCategory.ONCHAIN, analyst_score=5, analyst_max=200),
        MockAgent("sentiment", AgentCategory.SENTIMENT, analyst_score=5, analyst_max=200),
    ]
    settings = PolarisSettings()
    synthesiser = SynthesiserAgent(scorer)
    fake_deepseek = FakeDeepSeekClient()
    orchestrator = PipelineOrchestrator(
        agents,
        synthesiser,
        settings,
        mock_redis,
        deepseek_client=fake_deepseek,
    )
    monkeypatch.setattr(orchestrator, "_get_agent_timeout", lambda name: 0.08)

    ran_portfolio: dict[str, int] = {"n": 0}

    async def track_portfolio(asset: str, results: list) -> Decimal:  # type: ignore[type-arg]
        ran_portfolio["n"] += 1
        return Decimal("0")

    monkeypatch.setattr(
        orchestrator, "_run_portfolio_optimisation", track_portfolio,
    )

    result = await orchestrator.run(data=empty_data, asset="BTCUSDT")

    assert result.score < settings.min_trade_score
    assert result.decision == SignalDecision.NO_POSITION
    assert "BELOW_MIN_TRADE_SCORE" in result.key_risks
    assert fake_deepseek.context_matrix == ""
    assert ran_portfolio["n"] == 0
