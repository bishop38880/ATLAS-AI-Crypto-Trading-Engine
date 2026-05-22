"""Tests for Thompson Sampling."""

import asyncio

import numpy as np
import pytest
from fakeredis.aioredis import FakeRedis

from atlas.ml.thompson_sampling import ThompsonSampler


@pytest.fixture
async def redis():
    """Provide a FakeRedis instance."""
    client = FakeRedis(decode_responses=False)
    yield client
    await client.close()


@pytest.fixture
def sampler(redis):
    """Provide a ThompsonSampler instance."""
    return ThompsonSampler(redis)


@pytest.mark.asyncio
async def test_initialization(sampler, redis):
    """Test hash is initialized with alpha=1.0, beta=1.0 on first update."""
    await sampler.update("new_agent", was_correct=True)
    
    data = await redis.hgetall("thompson:new_agent")
    # FakeRedis might return bytes when decode_responses=False
    # but we will check string conversion
    assert float(data[b"alpha"]) == 2.0
    assert float(data[b"beta"]) == 1.0


@pytest.mark.asyncio
async def test_uniform_prior(sampler):
    """Test uniform prior Beta(1,1) -> approximately equal weights."""
    agents = ["a1", "a2", "a3"]
    weights = await sampler.get_expected_weights(agents)
    
    assert sum(weights.values()) == pytest.approx(1.0)
    for w in weights.values():
        assert w == pytest.approx(1.0 / 3.0)

    # Sample weights should also sum to 1.0
    sampled = await sampler.sample_weights(agents)
    assert sum(sampled.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_accuracy_weighting(sampler):
    """Test 90% accuracy agent gets highest weight."""
    agents = ["good", "bad", "average"]
    
    # good agent: 9 correct, 1 wrong
    for _ in range(9): await sampler.update("good", True)
    for _ in range(1): await sampler.update("good", False)
    
    # bad agent: 1 correct, 9 wrong
    for _ in range(1): await sampler.update("bad", True)
    for _ in range(9): await sampler.update("bad", False)
    
    # average agent: 5 correct, 5 wrong
    for _ in range(5): await sampler.update("average", True)
    for _ in range(5): await sampler.update("average", False)
    
    weights = await sampler.get_expected_weights(agents)
    
    assert weights["good"] > weights["average"]
    assert weights["average"] > weights["bad"]
    assert sum(weights.values()) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_decay_mechanics(sampler, redis):
    """Test decay reduces influence but never pushes below 1.0 floor."""
    await sampler.update("test", True)
    await sampler.update("test", True)
    
    data = await redis.hgetall("thompson:test")
    assert float(data[b"alpha"]) == 3.0
    assert float(data[b"beta"]) == 1.0
    
    # FakeRedis might not support eval without lua dependencies, so mock it for the test
    async def mock_eval(script, numkeys, key, decay_factor_str, timestamp_str):
        data = await redis.hgetall(key)
        alpha = float(data.get(b"alpha", 1.0))
        beta = float(data.get(b"beta", 1.0))
        decay = float(decay_factor_str)
        new_alpha = max(1.0, alpha * decay)
        new_beta = max(1.0, beta * decay)
        await redis.hset(key, mapping={"alpha": new_alpha, "beta": new_beta, "last_decay_at": timestamp_str})
        return [str(new_alpha), str(new_beta)]
        
    sampler._redis.eval = mock_eval
    
    # Apply decay of 0.5
    await sampler.apply_decay("test", decay_factor=0.5)
    
    data = await redis.hgetall("thompson:test")
    assert float(data[b"alpha"]) == 1.5
    assert float(data[b"beta"]) == 1.0 # 1.0 * 0.5 = 0.5 -> max(1.0, 0.5) = 1.0
    
    # Apply decay again
    await sampler.apply_decay("test", decay_factor=0.5)
    data = await redis.hgetall("thompson:test")
    assert float(data[b"alpha"]) == 1.0 # 1.5 * 0.5 = 0.75 -> max(1.0, 0.75) = 1.0
    assert float(data[b"beta"]) == 1.0


@pytest.mark.asyncio
async def test_concurrent_updates(sampler, redis):
    """Test 100 concurrent update() calls on the same agent."""
    tasks = [sampler.update("concurrent", True) for _ in range(100)]
    await asyncio.gather(*tasks)
    
    data = await redis.hgetall("thompson:concurrent")
    assert float(data[b"alpha"]) == 101.0
    assert float(data[b"beta"]) == 1.0
