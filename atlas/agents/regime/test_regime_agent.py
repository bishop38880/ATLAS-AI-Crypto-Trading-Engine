"""Tests for Market Regime Agent."""

import asyncio

import numpy as np
import pytest

from atlas.agents.base import AgentCategory, SignalDirection
from atlas.agents.regime.regime_agent import MarketRegimeAgent


@pytest.fixture
def dummy_context() -> dict[str, list[float]]:
    """Create dummy OHLCV data with enough bars (>= 90), mild uptrend (stable HMM fit)."""
    n_bars = 100
    rng = np.random.default_rng(42)
    drift = np.linspace(0.0, 0.35, n_bars)
    noise = rng.normal(0, 0.003, n_bars)
    close_prices = 100.0 * np.exp(drift + np.cumsum(noise))

    return {
        "close": close_prices.tolist(),
        "high": (close_prices * 1.002).tolist(),
        "low": (close_prices * 0.998).tolist(),
        "volume": rng.uniform(1000.0, 5000.0, n_bars).tolist(),
    }


@pytest.fixture
def insufficient_context() -> dict[str, list[float]]:
    """Create dummy OHLCV data with insufficient bars (< 90)."""
    return {
        "close": [100.0] * 50,
        "high": [101.0] * 50,
        "low": [99.0] * 50,
        "volume": [1000.0] * 50,
    }


@pytest.mark.asyncio
async def test_regime_agent_insufficient_data(insufficient_context: dict[str, list[float]]) -> None:
    """Test agent returns neutral with insufficient data."""
    agent = MarketRegimeAgent()
    
    assert agent.name == "regime"
    assert agent.category == AgentCategory.CONTEXT
    
    result = await agent.score({}, context=insufficient_context)
    
    assert result.agent_name == "regime"
    assert result.direction == SignalDirection.NEUTRAL
    assert result.score == 0
    assert "insufficient data" in result.explanation


@pytest.mark.asyncio
async def test_regime_agent_success(dummy_context: dict[str, list[float]]) -> None:
    """Test agent correctly computes features and returns regime info."""
    agent = MarketRegimeAgent()
    
    result = await agent.score({}, context=dummy_context)
    
    assert result.agent_name == "regime"
    assert result.score >= 0 and result.score <= 12
    assert result.direction in (
        SignalDirection.NEUTRAL,
        SignalDirection.BULLISH,
        SignalDirection.BEARISH,
    )
    
    assert "regime" in result.sub_signals
    assert "probabilities" in result.sub_signals
    assert "duration" in result.sub_signals
    assert "transition_prob" in result.sub_signals
    
    assert isinstance(result.sub_signals["regime"], str)
    assert isinstance(result.sub_signals["probabilities"], dict)
    
    probs = result.sub_signals["probabilities"]
    assert sum(probs.values()) == pytest.approx(1.0)
    for p in probs.values():
        assert isinstance(p, float)
