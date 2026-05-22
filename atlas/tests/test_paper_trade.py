import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import msgspec
import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient
from fakeredis.aioredis import FakeRedis

from backend.main import app
from atlas.dependencies import get_redis
from atlas.settings import polaris_settings
from atlas.models.paper_trade import (
    DemoSymbol,
    PaperTradeAck,
    PaperTradeInstruction,
)


@pytest.fixture
async def redis():
    """Create a clean fakeredis instance for each test."""
    r = FakeRedis()
    yield r
    await r.flushall()
    await r.aclose()


@pytest.fixture
async def client(redis):
    """Create an AsyncClient with the Redis dependency overridden."""
    app.dependency_overrides[get_redis] = lambda: redis
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    app.dependency_overrides.clear()


# ────────────────────────────────────────────────────────────────────
# GET /symbols
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_symbols_empty_when_cache_missing(client):
    """If Redis is empty, return empty list with None age."""
    resp = await client.get("/api/paper-trade/symbols")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["symbols"] == []
    assert data["cache_age_seconds"] is None


@pytest.mark.asyncio
async def test_get_symbols_returns_cached_list(client, redis):
    """Prime cache and verify it is returned with approximate age."""
    now = datetime.now(timezone.utc)
    symbols = [
        DemoSymbol(
            symbol="SBTCSUSDT",
            base_symbol="BTCUSDT",
            margin_coin="SUSDT",
            contract_type="SUSDT-FUTURES",
            fetched_at=now,
        ),
        DemoSymbol(
            symbol="SETHSUSDT",
            base_symbol="ETHUSDT",
            margin_coin="SUSDT",
            contract_type="SUSDT-FUTURES",
            fetched_at=now,
        ),
    ]
    await redis.set(
        polaris_settings.paper_trade_symbol_cache_key,
        msgspec.json.encode([s.model_dump() for s in symbols]),
    )

    resp = await client.get("/api/paper-trade/symbols")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["symbols"]) == 2
    assert data["symbols"][0]["base_symbol"] == "BTCUSDT"
    assert data["cache_age_seconds"] is not None
    assert data["cache_age_seconds"] >= 0


@pytest.mark.asyncio
async def test_get_symbols_corrupt_cache_returns_empty(client, redis):
    """Corrupt JSON in Redis should not crash the endpoint."""
    await redis.set(polaris_settings.paper_trade_symbol_cache_key, b"not valid json")
    resp = await client.get("/api/paper-trade/symbols")
    assert resp.status_code == 200
    assert resp.json()["symbols"] == []


# ────────────────────────────────────────────────────────────────────
# POST /
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_returns_503_when_cache_missing(client):
    """Cannot validate symbol if cache is missing."""
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "size_usd_notional": "100.0",
        "leverage": 5,
    }
    resp = await client.post("/api/paper-trade", json=payload)
    assert resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert resp.json()["detail"] == "demo_symbol_cache_unavailable"


@pytest.mark.asyncio
async def test_post_rejects_unknown_symbol(client, redis):
    """If symbol not in cache, return rejected status."""
    now = datetime.now(timezone.utc)
    symbols = [DemoSymbol(
        symbol="SBTCSUSDT", base_symbol="BTCUSDT",
        margin_coin="SUSDT", contract_type="SUSDT-FUTURES", fetched_at=now,
    )]
    await redis.set(
        polaris_settings.paper_trade_symbol_cache_key, msgspec.json.encode([s.model_dump() for s in symbols]),
    )

    payload = {
        "symbol": "DOGEUSDT",
        "side": "LONG",
        "size_usd_notional": "100.0",
        "leverage": 5,
    }
    resp = await client.post("/api/paper-trade", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "rejected"
    assert "DOGEUSDT_not_in_demo_universe" in data["reason"]


@pytest.mark.asyncio
async def test_post_publishes_instruction_to_pubsub(client, redis):
    """Verify PaperTradeInstruction is published to the correct channel."""
    now = datetime.now(timezone.utc)
    symbols = [DemoSymbol(
        symbol="SBTCSUSDT", base_symbol="BTCUSDT",
        margin_coin="SUSDT", contract_type="SUSDT-FUTURES", fetched_at=now,
    )]
    await redis.set(
        polaris_settings.paper_trade_symbol_cache_key, msgspec.json.encode([s.model_dump() for s in symbols]),
    )

    # Listen on the target channel
    pubsub = redis.pubsub()
    channel = f"{polaris_settings.paper_trade_redis_channel}:BTCUSDT"
    await pubsub.subscribe(channel)

    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "size_usd_notional": "100.00",
        "leverage": 10,
    }
    
    # Run POST in background as it will wait for an ack we haven't sent
    post_task = asyncio.create_task(client.post("/api/paper-trade", json=payload))
    
    # Wait for message on pubsub
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = msgspec.json.decode(message["data"])
            instr = PaperTradeInstruction(**data)
            assert instr.base_symbol == "BTCUSDT"
            assert instr.demo_symbol == "SBTCSUSDT"
            assert instr.size_usd_notional == Decimal("100.00")
            assert instr.leverage == 10
            break
            
    await pubsub.unsubscribe(channel)
    await pubsub.aclose()
    post_task.cancel()


async def _run_executor_simulator(redis, ready_event):
    ps = redis.pubsub()
    await ps.subscribe(f"{polaris_settings.paper_trade_redis_channel}:BTCUSDT")
    ready_event.set()
    async for msg in ps.listen():
        if msg["type"] != "message": continue
        instr = PaperTradeInstruction(**msgspec.json.decode(msg["data"]))
        ack = PaperTradeAck(order_id=instr.order_id, status="executed", bitget_order_id="42",
                            acknowledged_at=datetime.now(timezone.utc))
        await redis.publish(f"{polaris_settings.paper_trade_redis_channel}:ack:{instr.order_id}", 
                            msgspec.json.encode(ack.model_dump()))
        break
    await ps.aclose()


async def _seed_paper_trade_symbols(redis):
    now = datetime.now(timezone.utc)
    s = [DemoSymbol(symbol="SBTCSUSDT", base_symbol="BTCUSDT", margin_coin="SUSDT",
                    contract_type="SUSDT-FUTURES", fetched_at=now)]
    await redis.set(polaris_settings.paper_trade_symbol_cache_key, 
                    msgspec.json.encode([x.model_dump() for x in s]))


@pytest.mark.asyncio
async def test_post_returns_executed_when_ack_arrives(client, redis):
    """Simulate an executor that picks up the instruction and acks it."""
    await _seed_paper_trade_symbols(redis)
    ready = asyncio.Event()
    sim_task = asyncio.create_task(_run_executor_simulator(redis, ready))
    await ready.wait()

    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "size_usd_notional": "100.0",
        "leverage": 5,
    }
    resp = await client.post("/api/paper-trade", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "executed"
    assert data["bitget_order_id"] == "42"
    
    await sim_task


@pytest.mark.asyncio
async def test_post_returns_pending_on_ack_timeout(client, redis):
    """If no ack within timeout, return pending."""
    now = datetime.now(timezone.utc)
    symbols = [DemoSymbol(
        symbol="SBTCSUSDT", base_symbol="BTCUSDT",
        margin_coin="SUSDT", contract_type="SUSDT-FUTURES", fetched_at=now,
    )]
    await redis.set(
        polaris_settings.paper_trade_symbol_cache_key, msgspec.json.encode([s.model_dump() for s in symbols]),
    )

    # Set timeout very low for test
    polaris_settings.paper_trade_ack_wait_seconds = 0.1

    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "size_usd_notional": "100.0",
        "leverage": 5,
    }
    resp = await client.post("/api/paper-trade", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"


@pytest.mark.asyncio
async def test_post_decimal_round_trip_no_float(client, redis):
    """Verify that Decimal precision is preserved on the wire."""
    now = datetime.now(timezone.utc)
    symbols = [DemoSymbol(
        symbol="SBTCSUSDT", base_symbol="BTCUSDT",
        margin_coin="SUSDT", contract_type="SUSDT-FUTURES", fetched_at=now,
    )]
    await redis.set(
        polaris_settings.paper_trade_symbol_cache_key, msgspec.json.encode([s.model_dump() for s in symbols]),
    )

    pubsub = redis.pubsub()
    channel = f"{polaris_settings.paper_trade_redis_channel}:BTCUSDT"
    await pubsub.subscribe(channel)

    # High precision decimal
    val = "0.123456789"
    payload = {
        "symbol": "BTCUSDT",
        "side": "LONG",
        "size_usd_notional": val,
        "leverage": 5,
    }
    
    post_task = asyncio.create_task(client.post("/api/paper-trade", json=payload))
    
    async for message in pubsub.listen():
        if message["type"] == "message":
            # Check raw bytes to ensure no float conversion happened
            raw_data = message["data"].decode()
            assert val in raw_data
            
            # Check decoded value
            data = msgspec.json.decode(message["data"])
            instr = PaperTradeInstruction(**data)
            assert instr.size_usd_notional == Decimal(val)
            break
            
    await pubsub.aclose()
    post_task.cancel()
