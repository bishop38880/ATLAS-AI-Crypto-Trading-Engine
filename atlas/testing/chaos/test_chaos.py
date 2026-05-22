"""Chaos testing suite for ATLAS FINCON 8-agent hierarchy."""

import asyncio
import typing
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

import httpx

import pytest
from pydantic import SecretStr

from atlas.agents.base import BaseAgent

from atlas.agents.technical.technical_agent import TechnicalAgent
from atlas.agents.derivatives.derivatives_agent import DerivativesAgent
from atlas.agents.onchain.onchain_agent import OnChainAgent
from atlas.agents.sentiment.sentiment_agent import SentimentNewsAgent
from atlas.agents.regime.regime_agent import MarketRegimeAgent
from atlas.agents.risk.risk_agent import RiskAgent
from atlas.agents.synthesiser.synthesiser_agent import SynthesiserAgent
from atlas.orchestrator.scorer import ConfluenceScorer
from atlas.pipeline.orchestrator import PipelineOrchestrator
from atlas.providers.hydra.listener import HydraStreamListener
from atlas.shared.config import PolarisSettings
from atlas.testing.chaos.experiments import EXPERIMENTS, get_injector_for_experiment
from atlas.testing.chaos.framework import ChaosRunner, ChaosResult


# ---------------------------------------------------------------------------
# Mock setup for full hierarchy
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_redis() -> AsyncMock:
    mock = AsyncMock()
    # Basic list/set/get mock
    mock.pipeline.return_value = mock
    mock.execute.return_value = [b"1", b"1", [], b""]
    mock.get.return_value = None
    return mock

@pytest.fixture
def mock_hydra() -> MagicMock:
    listener = MagicMock(spec=HydraStreamListener)
    listener.get_latest_event.return_value = None
    return listener

@pytest.fixture
def base_agents(mock_redis: AsyncMock, mock_hydra: MagicMock) -> list[BaseAgent]:
    # FINCON 8-agent hierarchy: 5 Tier-1, Risk, Portfolio (merged in Risk), Synthesiser
    return [
        TechnicalAgent(),
        DerivativesAgent(),
        OnChainAgent(),
        SentimentNewsAgent(),
        MarketRegimeAgent(),
        RiskAgent(mock_redis, mock_hydra),
    ]

@pytest.fixture
def synthesiser() -> SynthesiserAgent:
    settings = PolarisSettings()
    scorer = ConfluenceScorer(settings=settings)
    return SynthesiserAgent(scorer)

@pytest.fixture
def orchestrator(base_agents: list[BaseAgent], synthesiser: SynthesiserAgent, mock_redis: AsyncMock) -> PipelineOrchestrator:
    settings = PolarisSettings()
    return PipelineOrchestrator(base_agents, synthesiser, settings, mock_redis)

@pytest.fixture(autouse=True)
def mock_http_pool() -> typing.Generator[None, None, None]:
    """Mock ProviderHttpPool to return successful responses by default."""
    async def _mock_get(self, path: str, params: dict | None = None) -> httpx.Response:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        # Valid empty/default json
        resp.content = b'{"data": [], "result": [], "value": "0"}'
        resp.raise_for_status = MagicMock()
        return resp

    with patch("atlas.shared.http_pool.ProviderHttpPool.get", new=_mock_get):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("experiment_name", EXPERIMENTS.keys())
async def test_chaos_experiment(experiment_name: str, orchestrator: PipelineOrchestrator, tmp_path):
    """Run all 9 chaos experiments."""
    experiment = EXPERIMENTS[experiment_name]
    injector = get_injector_for_experiment(experiment_name)
    
    runner = ChaosRunner(orchestrator, injector)
    result = await runner.run_experiment(experiment)
    
    # Write to isolated report
    report_file = tmp_path / f"chaos_{experiment_name}.md"
    with open(report_file, "w") as f:
        f.write(f"# {result.experiment_name}\\n")
        f.write(f"Verdict: {result.final_verdict}\\n")
        f.write(f"Crashed: {result.system_crashed}\\n")
        f.write(f"Cycles (Fault): {result.cycles_during_fault}\\n")
        f.write(f"Recovery: {result.recovery_time_seconds}s\\n")

    # Assertions
    assert result.system_crashed is False, f"Experiment {experiment_name} crashed the system."
    assert len(result.unhandled_exceptions) == 0, "Unhandled exceptions found."
    
    if "down" in experiment.fault_type or "timeout" in experiment.fault_type:
        assert result.avg_confidence_during_fault <= result.avg_confidence_after_recovery, \
            "Confidence did not degrade during fault."
