"""Unit tests for the multi-timeframe gate chain."""

from __future__ import annotations

import pytest

from backend.config.pipeline_config import TIMEFRAME_CONFLUENCE_BONUS
from backend.pipeline.gate_chain import GateChain
from backend.pipeline.gate_score import Direction, GateScore, Regime


class _MockScorer:
    async def score(self, symbol: str, timeframe: str) -> GateScore:
        return GateScore(
            timeframe=timeframe,
            score=155,
            direction=Direction.LONG,
            direction_confidence=0.72,
            regime=Regime.TRENDING,
            derivatives_score=60,
            whale_score=45,
            technical_score=12,
            sentiment_score=0,
            macro_score=28,
            funding_rate=0.0012,
            oi_change_pct=2.3,
        )


class _RangingDailyScorer:
    async def score(self, symbol: str, timeframe: str) -> GateScore:
        regime = Regime.RANGING if timeframe == "daily" else Regime.TRENDING
        return GateScore(
            timeframe=timeframe,
            score=140,
            direction=Direction.LONG,
            direction_confidence=0.70,
            regime=regime,
        )


class _MockRedis:
    async def get(self, key: str) -> None:
        return None


@pytest.mark.asyncio
async def test_gate_chain_passes_with_timeframe_bonus() -> None:
    chain = GateChain(_MockRedis(), _MockScorer())
    result = await chain.evaluate("BTC")
    assert result.passed is True
    assert result.final_score == 155 + TIMEFRAME_CONFLUENCE_BONUS
    assert result.timeframe_bonus == TIMEFRAME_CONFLUENCE_BONUS
    assert result.final_direction == Direction.LONG


@pytest.mark.asyncio
async def test_gate_chain_fails_daily_ranging_regime() -> None:
    chain = GateChain(_MockRedis(), _RangingDailyScorer())
    result = await chain.evaluate("ETH")
    assert result.passed is False
    assert result.fail_at_gate == "daily"
    assert result.fail_reason is not None
    assert "RANGING" in result.fail_reason


@pytest.mark.asyncio
async def test_gate_chain_fails_direction_conflict_with_daily() -> None:
    class _ConflictScorer:
        async def score(self, symbol: str, timeframe: str) -> GateScore:
            direction = Direction.SHORT if timeframe == "4hr" else Direction.LONG
            return GateScore(
                timeframe=timeframe,
                score=155,
                direction=direction,
                direction_confidence=0.72,
                regime=Regime.TRENDING,
            )

    chain = GateChain(_MockRedis(), _ConflictScorer())
    result = await chain.evaluate("SOL")
    assert result.passed is False
    assert result.fail_at_gate == "4hr"
