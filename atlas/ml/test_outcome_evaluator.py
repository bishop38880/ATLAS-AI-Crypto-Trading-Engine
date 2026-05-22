"""Tests for Outcome Evaluator."""

import asyncio

import msgspec
import pytest
from fakeredis.aioredis import FakeRedis

from atlas.ml.outcome_evaluator import OutcomeEvaluator


@pytest.fixture
async def redis():
    """Provide a FakeRedis instance."""
    client = FakeRedis(decode_responses=False)
    yield client
    await client.close()


@pytest.mark.asyncio
async def test_outcome_evaluator_agents(redis):
    """Test that correct agents receive positive updates."""
    evaluator = OutcomeEvaluator(redis, "postgresql://fake") # type: ignore
    
    breakdown = {
        "good_bull": {"direction": "bullish"},
        "good_bear": {"direction": "bearish"},
        "bad_bull": {"direction": "bullish"},
        "bad_bear": {"direction": "bearish"},
        "neutral_agent": {"direction": "neutral"},
        "unknown_agent": {},
    }
    breakdown_json = msgspec.json.encode(breakdown)
    
    from decimal import Decimal
    # Evaluate a positive PnL (Bullish is correct, Bearish is wrong)
    await evaluator._evaluate_agents(breakdown_json, Decimal("5.0"))
    
    # good_bull -> true
    data = await redis.hgetall("thompson:good_bull")
    assert float(data[b"alpha"]) == 2.0
    assert float(data[b"beta"]) == 1.0
    
    # good_bear predicted bearish, but PnL was > 0, so it was wrong
    data = await redis.hgetall("thompson:good_bear")
    assert float(data[b"alpha"]) == 1.0
    assert float(data[b"beta"]) == 2.0
    
    # neutral agent shouldn't be updated
    data = await redis.hgetall("thompson:neutral_agent")
    assert not data
