"""Tests for ProviderHealthTracker."""

import pytest

from atlas.core.provider_health import ProviderHealthTracker


@pytest.fixture
def mock_redis(mocker, monkeypatch):
    import fakeredis

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    fake = fakeredis.FakeAsyncRedis()
    mocker.patch("redis.asyncio.from_url", return_value=fake)
    return fake


@pytest.mark.asyncio
async def test_health_perfect(mock_redis) -> None:
    tracker = ProviderHealthTracker(redis_client=mock_redis)
    for _ in range(10):
        # 100% success, fast latency (0.01s < 0.2s threshold for coinalyze)
        await tracker.record_request("coinalyze", True, 0.01)

    health = await tracker.get_health("coinalyze")
    assert health == 1.0


@pytest.mark.asyncio
async def test_health_poor(mock_redis) -> None:
    tracker = ProviderHealthTracker(redis_client=mock_redis)
    # 50% success, and all have terrible latency
    # Coinalyze threshold is 0.2s, we use 0.5s to incur max latency penalty
    for i in range(10):
        success = i % 2 == 0
        await tracker.record_request("coinalyze", success, 0.5)

    health = await tracker.get_health("coinalyze")
    # sr=0.5 -> 0.7 * 0.5 = 0.35
    # lp=1.0 -> 0.3 * (1.0 - 1.0) = 0.0
    # Expected: 0.35
    assert abs(health - 0.35) < 0.01


@pytest.mark.asyncio
async def test_adaptive_timeout_clamps(mock_redis) -> None:
    tracker = ProviderHealthTracker(redis_client=mock_redis)

    # Small latency (0.1s) * 3 = 0.3s -> should clamp to floor 2.0s
    await tracker.record_request("coinalyze", True, 0.1)
    timeout = await tracker.get_timeout("coinalyze")
    assert timeout == 2.0

    # Large latency (15.0s) * 3 = 45.0s -> should clamp to ceiling 30.0s
    for _ in range(50):
        await tracker.record_request("coinalyze", True, 15.0)

    timeout = await tracker.get_timeout("coinalyze")
    assert timeout == 30.0
