"""Tests for SentimentAgent — F&G blend, gate, and archetype sub-signals."""

from __future__ import annotations

import pytest

from atlas.agents.sentiment.sentiment_agent import SentimentAgent
from atlas.agents.base import SignalDirection


@pytest.fixture
def agent() -> SentimentAgent:
    return SentimentAgent()


@pytest.mark.asyncio
async def test_volume_gate_blocks_scoring(agent: SentimentAgent) -> None:
    """Social volume z-score below 2.0 yields zero score."""
    result = await agent.score(
        {},
        context={"vol_zscore": 1.5, "fear_greed_score": 10},
    )
    assert result.score == 0
    assert "VOLUME_GATE_BLOCKED" in result.risks


@pytest.mark.asyncio
async def test_extreme_fear_bullish_with_gate(agent: SentimentAgent) -> None:
    """Maximum fear with gate open produces bullish direction."""
    result = await agent.score(
        {},
        context={
            "vol_zscore": 3.0,
            "fear_greed_score": 10,
            "fear_greed_classification": "Extreme Fear",
            "polarity_percentile": 50.0,
            "funding_rate_zscore": 0.0,
        },
    )
    assert result.score > 0
    assert result.direction == SignalDirection.BULLISH
    assert "retail_despair" in result.sub_signals


@pytest.mark.asyncio
async def test_extreme_greed_bearish_with_gate(agent: SentimentAgent) -> None:
    """Maximum greed with gate open produces bearish direction."""
    result = await agent.score(
        {},
        context={
            "vol_zscore": 2.5,
            "fear_greed_score": 90,
            "fear_greed_classification": "Extreme Greed",
            "polarity_percentile": 50.0,
            "funding_rate_zscore": 0.0,
        },
    )
    assert result.score > 0
    assert result.direction == SignalDirection.BEARISH
    assert "fomo_peak" in result.sub_signals
