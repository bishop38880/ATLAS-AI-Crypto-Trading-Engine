"""Tests for the ProviderCircuitBreaker."""

import asyncio

import pytest

from atlas.core.circuit_breaker import (
    ProviderCircuitBreaker,
    STATE_CLOSED,
    STATE_OPEN,
)


@pytest.fixture
def mock_redis(mocker, monkeypatch):
    import fakeredis

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    fake = fakeredis.FakeAsyncRedis()
    mocker.patch("redis.asyncio.from_url", return_value=fake)
    return fake


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_failures(mock_redis) -> None:
    breaker = ProviderCircuitBreaker("test_prov", fail_max=5, reset_timeout_seconds=30)

    async def fail_func() -> None:
        raise ValueError("System error")

    for _ in range(5):
        with pytest.raises(ValueError):
            await breaker.call(fail_func, is_critical=True)

    # 6th call should immediately return None without raising ValueError
    res = await breaker.call(fail_func, is_critical=True)
    assert res is None
    assert breaker.current_state == STATE_OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_transition(mock_redis) -> None:
    breaker = ProviderCircuitBreaker("test_prov", fail_max=1, reset_timeout_seconds=0.1)

    async def fail_func() -> None:
        raise ValueError("System error")

    with pytest.raises(ValueError):
        await breaker.call(fail_func, is_critical=True)

    assert breaker.current_state == STATE_OPEN

    # Wait for reset timeout
    await asyncio.sleep(0.15)

    async def succ_func() -> str:
        return "success"

    # Should transition to half-open, execute, and then close
    res = await breaker.call(succ_func, is_critical=True)
    assert res == "success"
    assert breaker.current_state == STATE_CLOSED
