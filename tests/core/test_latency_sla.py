"""Tests for Phase 5 Latency SLA enforcement."""

import asyncio
import pytest

from atlas.core.latency_monitor import LatencyMonitor
from atlas.core.circuit_breaker import get_circuit_breaker, STATE_CLOSED, STATE_DEGRADED
from atlas.shared.config import PolarisSettings

@pytest.fixture
def mock_redis(mocker, monkeypatch):
    import fakeredis.aioredis
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    fake = fakeredis.aioredis.FakeRedis()
    mocker.patch("redis.asyncio.from_url", return_value=fake)
    return fake

@pytest.fixture
def settings():
    s = PolarisSettings()
    s.latency_budgets = {"test_stage": 0.1}
    return s

@pytest.mark.asyncio
async def test_latency_sla_violation_and_recovery(mock_redis, settings) -> None:
    monitor = LatencyMonitor(mock_redis, settings)
    breaker = get_circuit_breaker("test_stage")
    
    # Start closed
    await mock_redis.set("cb:test_stage:state", STATE_CLOSED)
    
    # Simulate 10 cycles of slow I/O (> 0.15s which is > 1.5x of 0.1s budget)
    # First 4 cycles are skipped (need 5 samples). Next 5 cycles will each increment viol_count.
    for _ in range(10):
        async with monitor.measure("test_stage"):
            await asyncio.sleep(0.16)
        await monitor.check_and_trigger()
        
    state = await mock_redis.get("cb:test_stage:state")
    assert state.decode() == STATE_DEGRADED, "Circuit breaker should have tripped to DEGRADED"
    
    # We need to run enough fast cycles to push all slow ones out of the 50-sample window.
    # Since P99 requires almost all 50 samples to be fast, we need ~49 fast samples to get a fast P99.
    # Then we need 10 more fast samples to trigger recovery. So 49 + 10 = 59 minimum.
    # We run 65 to be safe.
    for _ in range(65):
        async with monitor.measure("test_stage"):
            await asyncio.sleep(0.05)
        await monitor.check_and_trigger()
        
    state = await mock_redis.get("cb:test_stage:state")
    assert state.decode() == STATE_CLOSED, "Circuit breaker should have recovered to CLOSED"
