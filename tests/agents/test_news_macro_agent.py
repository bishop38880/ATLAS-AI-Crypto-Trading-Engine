"""Tests for NewsMacroAgent."""

import pytest

from atlas.agents.base import SignalDirection
from atlas.agents.news_macro.news_macro_agent import NewsMacroAgent

@pytest.fixture
def agent() -> NewsMacroAgent:
    """Provide a NewsMacroAgent instance."""
    return NewsMacroAgent()

@pytest.mark.asyncio
async def test_news_macro_high_impact_event(agent: NewsMacroAgent) -> None:
    """Test high-impact macroeconomic event yields -20 suppression."""
    context = {"fomc_within_24h": True}
    result = await agent.score({}, context)
    assert result.sub_signals["conviction_suppression"].metadata["amount"] == -20
    assert result.direction == SignalDirection.NEUTRAL
    assert result.score == 0

@pytest.mark.asyncio
async def test_news_macro_no_event(agent: NewsMacroAgent) -> None:
    """Test no event yields 0 suppression."""
    context = {"fomc_within_24h": False, "cliff_unlock_pct_7d": 0.0}
    result = await agent.score({}, context)
    assert result.sub_signals["conviction_suppression"].metadata["amount"] == 0

@pytest.mark.asyncio
async def test_news_macro_breaking_negative_news(agent: NewsMacroAgent) -> None:
    """Test negative breaking news yields -15 suppression."""
    context = {"cliff_unlock_pct_7d": 6.0}
    result = await agent.score({}, context)
    assert result.sub_signals["conviction_suppression"].metadata["amount"] == -15
    assert any("CLIFF token unlock" in risk for risk in result.risks)
