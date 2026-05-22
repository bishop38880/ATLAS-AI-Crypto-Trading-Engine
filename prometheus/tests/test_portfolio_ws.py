import asyncio
import json
import subprocess
import jwt
import pytest
import redis.asyncio as redis_asyncio
import uvicorn
import websockets
from fakeredis import FakeAsyncRedis
from loguru import logger
from pydantic import ValidationError
from redis.asyncio import Redis

from prometheus.api.main import app, settings
from prometheus.api._portfolio import PortfolioSnapshot


@pytest.fixture(autouse=True)
def _prometheus_portfolio_uses_fakeredis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Portfolio WS/REST builds Redis via `Redis.from_url`; use in-memory fake.

    Avoids connection errors when no local Redis is reachable (CI sandboxes,
    blocked loopback ports, etc.).
    """
    fake_client = FakeAsyncRedis(decode_responses=True)

    @classmethod
    def _from_url(
        cls,
        url: str = "",
        *,
        decode_responses: bool = False,
        **kwargs: object,
    ) -> FakeAsyncRedis:
        _ = (url, decode_responses, kwargs)
        return fake_client

    monkeypatch.setattr(redis_asyncio.Redis, "from_url", _from_url)


def generate_token(sub: str) -> str:
    return jwt.encode({"sub": sub}, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

@pytest.fixture
async def uvicorn_server():
    config = uvicorn.Config(app=app, host="127.0.0.1", port=8002, log_level="error")
    server = uvicorn.Server(config)
    
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.5)  # give it time to start
    
    yield "ws://127.0.0.1:8002"
    
    server.should_exit = True
    await task

def test_portfolio_relocation_complete():
    try:
        result = subprocess.run(
            ['rg', '-nw', 'portfolio|_build_portfolio_snapshot|portfolio_ws', 'atlas/'],
            capture_output=True,
            text=True
        )
    except FileNotFoundError:
        result = subprocess.run(
            ['grep', '-rnw', '-E', 'portfolio|_build_portfolio_snapshot|portfolio_ws', 'atlas/'],
            capture_output=True,
            text=True
        )
    
    lines = result.stdout.splitlines()
    violations = []
    for line in lines:
        if "test_" in line or "tests/" in line or "__pycache__" in line:
            continue
        # Provider data paths are allowed
        if "portfolio:" in line or "position:" in line or "order_id" in line:
            if "portfolio:" in line:
                continue
        # Words used in comments / non-endpoint contexts
        if "portfolio risk" in line or "portfolio equity" in line or "portfolio value" in line or "portfolio cap" in line or "portfolio constraints" in line or "portfolio —" in line or "portfolio exposure" in line or "portfolio_exposure" in line or "portfolio_direction" in line or "portfolio_correlation" in line or "portfolio_drawdown" in line or "PortfolioOptimisationAgent" in line or "run_portfolio_optimisation" in line or "Wallet portfolio" in line or "portfolio_optimisation_failed" in line:
            continue
            
        violations.append(line)
        
    assert not violations, f"Found portfolio references in ATLAS: {violations}"

@pytest.mark.asyncio
async def test_portfolio_ws_rejects_missing_token_with_4401(uvicorn_server):
    uri = f"{uvicorn_server}/dashboard/ws/portfolio"
    try:
        async with websockets.connect(uri) as ws:
            await ws.recv()
        assert False, "Should have closed"
    except websockets.exceptions.ConnectionClosed as e:
        assert e.code == 4401

@pytest.mark.asyncio
async def test_portfolio_ws_rejects_invalid_token_with_4401(uvicorn_server):
    uri = f"{uvicorn_server}/dashboard/ws/portfolio?token=GARBAGE"
    try:
        async with websockets.connect(uri) as ws:
            await ws.recv()
        assert False, "Should have closed"
    except websockets.exceptions.ConnectionClosed as e:
        assert e.code == 4401

@pytest.mark.asyncio
async def test_portfolio_ws_emits_camelcase_field_names(uvicorn_server):
    token = generate_token("user123")
    uri = f"{uvicorn_server}/dashboard/ws/portfolio?token={token}"
    
    redis = Redis.from_url(settings.redis_url)  # type: ignore[type-arg]
    snapshot_data = {
        "total_value": "1000.50",
        "daily_pnl": "50.0",
        "daily_pnl_percent": 5.0,
        "positions": [],
        "is_paper": True,
        "timestamp": "2026-04-27T00:00:00Z"
    }
    await redis.set("portfolio:user123:snapshot", json.dumps(snapshot_data))
    
    try:
        async with websockets.connect(uri) as ws:
            frame = await ws.recv()
            data = json.loads(frame)
            assert "dailyPnl" in data
            assert "dailyPnlPercent" in data
            assert "isPaper" in data
            assert "daily_pnl" not in data
            assert "daily_pnl_percent" not in data
            assert "is_paper" not in data
    finally:
        await redis.delete("portfolio:user123:snapshot")
        await redis.aclose()

@pytest.mark.asyncio
async def test_portfolio_snapshot_returns_paper_flag_when_absent(uvicorn_server):
    token = generate_token("user123")
    uri = f"{uvicorn_server}/dashboard/ws/portfolio?token={token}"
    
    redis = Redis.from_url(settings.redis_url)  # type: ignore[type-arg]
    await redis.delete("portfolio:user123:snapshot")
    
    try:
        async with websockets.connect(uri) as ws:
            frame = await ws.recv()
            data = json.loads(frame)
            assert data["isPaper"] is True
            assert data["totalValue"] == "0"
            assert data["positions"] == []
    finally:
        await redis.aclose()

class CaptureSink:
    def __init__(self):
        self.logs = []
    def write(self, message):
        self.logs.append(message.record)

@pytest.mark.asyncio
async def test_portfolio_snapshot_rejects_order_fields(uvicorn_server):
    # (a) Unit test
    bad_json = json.dumps({
        "total_value": "1000",
        "daily_pnl": "0",
        "daily_pnl_percent": 0.0,
        "positions": [],
        "is_paper": True,
        "timestamp": "2026-04-27T00:00:00Z",
        "order_id": "123",
        "api_key": "secret"
    })
    
    with pytest.raises(ValidationError) as exc_info:
        PortfolioSnapshot.model_validate_json(bad_json)
        
    errors = str(exc_info.value)
    assert "order_id" in errors
    assert "api_key" in errors
    assert "Extra inputs are not permitted" in errors
    
    # (b) Integration
    sink = CaptureSink()
    handler_id = logger.add(sink.write)
    
    token = generate_token("user_bad")
    uri = f"{uvicorn_server}/dashboard/ws/portfolio?token={token}"
    
    redis = Redis.from_url(settings.redis_url)  # type: ignore[type-arg]
    await redis.set("portfolio:user_bad:snapshot", bad_json)
    
    try:
        async with websockets.connect(uri) as ws:
            try:
                await ws.recv()
            except websockets.exceptions.ConnectionClosed:
                pass
                
        log_records = [r for r in sink.logs if r["message"] == "portfolio_snapshot_invalid"]
        assert len(log_records) > 0
    finally:
        logger.remove(handler_id)
        await redis.delete("portfolio:user_bad:snapshot")
        await redis.aclose()

@pytest.mark.asyncio
async def test_rest_snapshot_endpoint_uses_camel_aliases(uvicorn_server):
    import httpx
    token = generate_token("user123")
    http_uri = uvicorn_server.replace("ws://", "http://")
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{http_uri}/api/portfolio/snapshot",
            headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    data = response.json()
    assert "dailyPnl" in data
    assert "isPaper" in data
    assert "totalValue" in data
