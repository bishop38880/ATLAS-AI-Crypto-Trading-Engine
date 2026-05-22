"""Tests for RiskAgent."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
import msgspec
import redis.asyncio as redis


from atlas.agents.base import SignalDirection
from atlas.agents.risk.risk_agent import RiskAgent
from atlas.providers.hydra.listener import HydraStreamListener, HydraCascadeEvent


class MockRedis:
    def __init__(self, data: dict[str, bytes]) -> None:
        self._data = data

    async def get(self, key: str) -> bytes | None:
        return self._data.get(key)

    async def publish(self, channel: str, message: str) -> int:
        return 1


class MockHydraListener:
    def __init__(self, event: HydraCascadeEvent | None = None) -> None:
        self._event = event

    def get_latest_event(self, asset: str | None = None) -> HydraCascadeEvent | None:
        return self._event


@pytest.fixture
def empty_redis() -> MockRedis:
    return MockRedis({})


@pytest.fixture
def healthy_redis() -> MockRedis:
    return MockRedis({
        "portfolio:pnl_24h": b"-0.01",
        "portfolio:total_exposure_pct": b"0.5",
        "portfolio:open_positions": msgspec.json.encode([]),
        "correlation:BTC/USDT:max": b"0.5",
        "market:volatility_regime": b"low_volatility",
        "portfolio:consecutive_losses": b"2",
        "portfolio:recent_trades": b"5",
    })


@pytest.mark.asyncio
async def test_risk_agent_healthy(healthy_redis: MockRedis) -> None:
    agent = RiskAgent(healthy_redis, MockHydraListener()) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    assert result.veto is False
    assert result.score == 0
    assert result.max_score == 0
    assert len(result.risks) == 0


@pytest.mark.asyncio
async def test_risk_agent_missing_data_fails_open(empty_redis: MockRedis) -> None:
    agent = RiskAgent(empty_redis, MockHydraListener()) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    assert result.veto is False
    assert result.score == 0
    assert result.max_score == 0
    assert len(result.risks) == 0


@pytest.mark.asyncio
async def test_risk_agent_drawdown_veto() -> None:
    r = MockRedis({"portfolio:pnl_24h": b"-0.15"})
    agent = RiskAgent(r, MockHydraListener()) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    assert result.veto is True
    assert result.score == 0


@pytest.mark.asyncio
async def test_risk_agent_exposure_risk() -> None:
    r = MockRedis({"portfolio:total_exposure_pct": b"0.85"})
    agent = RiskAgent(r, MockHydraListener()) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    assert result.veto is False
    assert any("Exposure > 80%" in risk for risk in result.risks)


@pytest.mark.asyncio
async def test_risk_agent_cascade_risk(empty_redis: MockRedis) -> None:
    from datetime import datetime, timezone
    event = HydraCascadeEvent(
        event_id="test",
        asset="BTC/USDT",
        tier=4,
        exchanges=["binance"],
        total_liquidation_usd=Decimal("1000000"),
        timestamp=datetime.now(timezone.utc)
    )
    agent = RiskAgent(empty_redis, MockHydraListener(event)) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    assert result.veto is True
    assert any("Tier-4 cascade" in risk for risk in result.risks)


@pytest.mark.asyncio
async def test_redis_bytes_decoding_malformed() -> None:
    r = MockRedis({"portfolio:pnl_24h": b"not-a-number"})
    agent = RiskAgent(r, MockHydraListener()) # type: ignore
    result = await agent.score({}, {"asset": "BTC/USDT"})
    
    # Defaults to 0, no veto
    assert result.veto is False
