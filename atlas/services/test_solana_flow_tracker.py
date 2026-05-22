"""Tests for Solana flow tracker rolling windows."""

from __future__ import annotations

import time

import pytest
import fakeredis.aioredis

from atlas.providers.helius.models import FlowEvent
from atlas.services import solana_flow_tracker


@pytest.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=False)
    solana_flow_tracker.init_redis(client)
    yield client
    solana_flow_tracker.init_redis(None)  # type: ignore[arg-type]


def _sample_event(signature: str, direction: str = "inflow", usd: float = 75_000.0) -> FlowEvent:
    return FlowEvent(
        timestamp=time.time(),
        symbol="SOL",
        direction=direction,  # type: ignore[arg-type]
        amount_usd=usd,
        amount_native=500.0,
        source="webhook",
        exchange="binance",
        from_address="whale_addr",
        to_address="5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
        signature=signature,
    )


@pytest.mark.asyncio
async def test_record_flow_event_dedupes_signature(redis_client) -> None:
    event = _sample_event("sig_dedup_1")
    assert await solana_flow_tracker.record_flow_event(event) is True
    assert await solana_flow_tracker.record_flow_event(event) is False


@pytest.mark.asyncio
async def test_get_signals_empty_returns_neutral_zeros(redis_client) -> None:
    signals = await solana_flow_tracker.get_signals("SOL")
    assert signals is not None
    assert signals.whale_tx_count_24h == 0
    assert signals.flow_direction == "neutral"


@pytest.mark.asyncio
async def test_inflow_increases_netflow_and_count(redis_client) -> None:
    await solana_flow_tracker.record_flow_event(_sample_event("sig_in_1", "inflow", 60_000.0))
    signals = await solana_flow_tracker.get_signals("SOL")
    assert signals is not None
    assert signals.whale_tx_count_24h == 1
    assert signals.exchange_netflow_24h > 0


@pytest.mark.asyncio
async def test_outflow_decreases_netflow(redis_client) -> None:
    await solana_flow_tracker.record_flow_event(_sample_event("sig_out_1", "outflow", 80_000.0))
    signals = await solana_flow_tracker.get_signals("SOL")
    assert signals is not None
    assert signals.exchange_netflow_24h < 0
