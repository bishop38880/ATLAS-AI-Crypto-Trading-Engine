"""Tests for the Nightly Dream Cycle (Pattern Extractor)."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from atlas.rag.pattern_extractor import (
    OutcomeAggregator,
    PatternSynthesiser,
)


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    # Return two records: one in 'volatile', one in 'bull'
    pool.fetch.return_value = [
        {"signal_id": "1", "outcome_label": "LOSS", "regime": "volatile", "reasoning": "A"},
        {"signal_id": "2", "outcome_label": "WIN", "regime": "bull", "reasoning": "B"},
        {"signal_id": "3", "outcome_label": "LOSS", "regime": "volatile", "reasoning": "C"},
        {"signal_id": "4", "outcome_label": "SCRATCH", "regime": None, "reasoning": "D"},
    ]
    return pool


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    # Mock complete to return a valid JSON string wrapped in markdown
    mock_resp = MagicMock()
    mock_resp.text = """```json
[
  {
    "pattern_type": "Volatile Breakout Fakeout",
    "content_text": "Losing trades in volatile regimes often buy into initial breakouts.",
    "regime": "volatile",
    "ttl_hours": 168
  }
]
```"""
    llm.complete.return_value = mock_resp
    return llm


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock()
    return writer


@pytest.mark.asyncio
async def test_outcome_aggregator_grouping(mock_pool: AsyncMock) -> None:
    """Test that aggregator correctly groups trades by regime/classification."""
    aggregator = OutcomeAggregator(mock_pool)
    grouped = await aggregator.fetch_recent_outcomes(days=7)
    
    assert "volatile_LOSS" in grouped
    assert len(grouped["volatile_LOSS"]) == 2
    assert grouped["volatile_LOSS"][0]["signal_id"] == "1"
    
    assert "bull_WIN" in grouped
    assert len(grouped["bull_WIN"]) == 1
    
    # Null regime defaults to "unknown"
    assert "unknown_SCRATCH" in grouped
    assert len(grouped["unknown_SCRATCH"]) == 1


@pytest.mark.asyncio
async def test_synthesiser_handles_empty_data(mock_llm: AsyncMock, mock_writer: AsyncMock) -> None:
    """Test synthesiser handles empty data safely without calling LLM."""
    synth = PatternSynthesiser(mock_llm, mock_writer)
    patterns = await synth.synthesize_patterns({})
    assert len(patterns) == 0
    mock_llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_synthesiser_extracts_and_stores(mock_llm: AsyncMock, mock_writer: AsyncMock, mock_pool: AsyncMock) -> None:
    """Test patterns are extracted and passed to RAGWriter."""
    aggregator = OutcomeAggregator(mock_pool)
    synth = PatternSynthesiser(mock_llm, mock_writer)
    
    await synth.run_dream_cycle(aggregator, days=7)
    
    # 1. verify LLM was called
    mock_llm.complete.assert_called_once()
    
    # 2. verify Writer was called
    mock_writer.write_pattern.assert_called_once_with(
        title="Volatile Breakout Fakeout",
        content="Losing trades in volatile regimes often buy into initial breakouts.",
        category="volatile",
        source="dream_cycle",
    )


@pytest.mark.asyncio
async def test_synthesiser_handles_invalid_json(mock_llm: AsyncMock, mock_writer: AsyncMock) -> None:
    """Test synthesiser does not crash on bad JSON."""
    mock_resp = MagicMock()
    mock_resp.text = "Just some text, no json"
    mock_llm.complete.return_value = mock_resp
    
    synth = PatternSynthesiser(mock_llm, mock_writer)
    patterns = await synth.synthesize_patterns({"bull_WIN": [{"a": 1}]})
    assert len(patterns) == 0
