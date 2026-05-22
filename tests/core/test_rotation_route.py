import pytest
from httpx import ASGITransport, AsyncClient
from fakeredis.aioredis import FakeRedis

from backend.main import app
from atlas.dependencies import get_redis

@pytest.fixture
async def redis():
    r = FakeRedis()
    yield r
    await r.flushall()
    await r.aclose()

@pytest.fixture
async def client(redis):
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_get_rotation_state_success(client, redis):
    # Prime Redis: SMEMBERS returns set of bytes
    await redis.sadd("polaris:universe:all", "BTCUSDT", "ETHUSDT")
    await redis.sadd("polaris:rotation:active_33", "BTCUSDT")
    await redis.sadd("polaris:rotation:daily_8", "BTCUSDT")
    
    resp = await client.get("/api/rotation/state")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["full_universe"] == ["BTCUSDT", "ETHUSDT"]
    assert data["active_33"] == ["BTCUSDT"]
    assert data["daily_8"] == ["BTCUSDT"]
    assert data["is_stale"] is False
    assert "fetched_at" in data

@pytest.mark.asyncio
async def test_get_rotation_state_stale(client, redis):
    # One key missing
    await redis.sadd("polaris:universe:all", "BTCUSDT")
    # active_33 missing
    await redis.sadd("polaris:rotation:daily_8", "BTCUSDT")
    
    resp = await client.get("/api/rotation/state")
    assert resp.status_code == 200
    data = resp.json()
    
    assert data["is_stale"] is True


@pytest.mark.asyncio
async def test_get_rotation_state_redis_failure_returns_empty_stale() -> None:
    """Redis I/O errors must not 500; UI relies on this for graceful degradation."""

    class _FailingPipeline:
        def smembers(self, _key: str) -> "_FailingPipeline":
            return self

        async def execute(self) -> None:
            raise ConnectionError("simulated redis failure")

    class _FailingRedis:
        def pipeline(self) -> _FailingPipeline:
            return _FailingPipeline()

    app.dependency_overrides[get_redis] = lambda: _FailingRedis()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as test_client:
            resp = await test_client.get("/api/rotation/state")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert data["full_universe"] == []
    assert data["active_33"] == []
    assert data["daily_8"] == []
    assert data["is_stale"] is True
