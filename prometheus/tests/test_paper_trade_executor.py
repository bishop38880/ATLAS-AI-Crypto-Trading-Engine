import asyncio
import msgspec
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
from fakeredis import FakeAsyncRedis

from prometheus.execution.bitget_client import BitgetExecutionClient, PlaceOrderResult
from prometheus.services.paper_trade_executor import PaperTradeExecutor
from prometheus.services.paper_trade_models import (
    PaperTradeInstruction,
    PaperTradeAck,
)
from prometheus.settings import prometheus_settings


@pytest.fixture
def redis():
    return FakeAsyncRedis()


@pytest.fixture
def bitget_client():
    client = MagicMock(spec=BitgetExecutionClient)
    client.usd_notional_to_base_coin_size = AsyncMock(return_value=Decimal("0.001"))
    client.place_plan_order = AsyncMock(return_value=PlaceOrderResult(success=True, order_id="42"))
    return client


@pytest.fixture
def settings():
    # Use a non-frozen mock for testing
    settings = MagicMock()
    settings.paper_trade_enabled = False
    settings.paper_trade_dry_run = True
    settings.bitget_demo_write_enabled = False
    settings.paper_trade_redis_channel = "polaris:paper_trade"
    return settings


@pytest.fixture
async def executor(redis, bitget_client, settings):
    async with httpx.AsyncClient() as http_client:
        yield PaperTradeExecutor(redis, http_client, bitget_client, settings)


async def _wait_for_ack(pubsub, timeout_steps=20):
    for _ in range(timeout_steps):
        message = await pubsub.get_message(ignore_subscribe_messages=True)
        if message: return message
        await asyncio.sleep(0.1)
    return None

async def _seed_symbols(redis, symbol="SBTCSUSDT", base="BTCUSDT"):
    from prometheus.services.demo_symbols import REDIS_CACHE_KEY, DemoSymbol
    s = DemoSymbol(symbol=symbol, base_symbol=base, margin_coin="SUSDT",
                   contract_type="SUSDT-FUTURES", fetched_at=datetime.now(timezone.utc))
    await redis.set(REDIS_CACHE_KEY, msgspec.json.encode([s.model_dump()]))

from typing import Literal
def _make_ins(order_id="test", side: Literal["LONG", "SHORT", "CLOSE"] = "LONG", version="1.0.0"):
    return PaperTradeInstruction(
        schema_version=version, order_id=order_id, base_symbol="BTCUSDT",
        demo_symbol="SBTCSUSDT", margin_coin="SUSDT", side=side,
        size_usd_notional=Decimal("100"), leverage=5,
        requested_at=datetime.now(timezone.utc), source="test"
    )

@pytest.mark.asyncio
async def test_executor_rejects_unsupported_schema_version(redis, executor):
    ins = _make_ins(version="2.0.0")
    task = asyncio.create_task(executor.run())
    await asyncio.sleep(0.1)
    
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"polaris:paper_trade:ack:{ins.order_id}")
    await redis.publish(f"polaris:paper_trade:{ins.base_symbol}", msgspec.json.encode(ins.model_dump()))
    
    message = await _wait_for_ack(pubsub)
    assert message and "rejected" in message["data"].decode()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
async def test_executor_dry_run_when_paper_trade_disabled(redis, executor, bitget_client, settings):
    settings.paper_trade_dry_run = True
    settings.bitget_demo_write_enabled = False
    ins = _make_ins(order_id="test-dry-run")
    await _seed_symbols(redis)

    task = asyncio.create_task(executor.run())
    await asyncio.sleep(0.1)
    
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"polaris:paper_trade:ack:{ins.order_id}")
    await redis.publish(f"polaris:paper_trade:{ins.base_symbol}", msgspec.json.encode(ins.model_dump()))
    
    message = await _wait_for_ack(pubsub)
    assert message and "accepted" in message["data"].decode()
    bitget_client.place_plan_order.assert_not_called()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
async def test_executor_accepts_without_write_when_demo_write_disabled(
    redis,
    executor,
    bitget_client,
    settings,
):
    settings.paper_trade_dry_run = False
    settings.bitget_demo_write_enabled = False
    ins = _make_ins(order_id="test-demo-disabled")
    await _seed_symbols(redis)

    task = asyncio.create_task(executor.run())
    await asyncio.sleep(0.1)

    pubsub = redis.pubsub()
    await pubsub.subscribe(f"polaris:paper_trade:ack:{ins.order_id}")
    await redis.publish(f"polaris:paper_trade:{ins.base_symbol}", msgspec.json.encode(ins.model_dump()))

    message = await _wait_for_ack(pubsub)
    assert message and "bitget_demo_write_disabled" in message["data"].decode()
    bitget_client.place_plan_order.assert_not_called()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

@pytest.mark.asyncio
async def test_executor_calls_bitget_when_demo_write_enabled(redis, executor, bitget_client, settings):
    settings.paper_trade_dry_run = False
    settings.bitget_demo_write_enabled = True
    ins = _make_ins(order_id="test-enabled")
    await _seed_symbols(redis)

    task = asyncio.create_task(executor.run())
    await asyncio.sleep(0.1)
    
    pubsub = redis.pubsub()
    await pubsub.subscribe(f"polaris:paper_trade:ack:{ins.order_id}")
    await redis.publish(f"polaris:paper_trade:{ins.base_symbol}", msgspec.json.encode(ins.model_dump()))
    
    message = await _wait_for_ack(pubsub)
    assert message and "executed" in message["data"].decode()
    bitget_client.place_plan_order.assert_called_once()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
