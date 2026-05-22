"""Tests for VolatilityMonitor."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
import pytest

from atlas.providers.hydra.listener import HydraStreamListener, HydraCascadeEvent
from atlas.core.volatility_monitor import VolatilityMonitor

@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()

@pytest.fixture
def mock_hydra() -> MagicMock:
    hydra = MagicMock(spec=HydraStreamListener)
    hydra._buffer = {}
    return hydra

@pytest.fixture
def monitor(mock_hydra: MagicMock, mock_redis: AsyncMock) -> VolatilityMonitor:
    return VolatilityMonitor(mock_hydra, mock_redis)

def create_event(vol_1h: float | None, vol_30d: float | None) -> HydraCascadeEvent:
    return HydraCascadeEvent(
        event_id="test",
        asset="BTC/USDT",
        tier=1,
        exchanges=["binance"],
        total_liquidation_usd=Decimal("1000.0"),
        timestamp=datetime.now(timezone.utc),
        current_1h_volatility=vol_1h,
        average_30d_volatility=vol_30d,
    )

@pytest.mark.asyncio
async def test_get_vol_ratio_calculates_correctly(monitor: VolatilityMonitor, mock_hydra: MagicMock, mock_redis: AsyncMock) -> None:
    mock_hydra.get_latest_event.return_value = create_event(vol_1h=20.0, vol_30d=10.0)
    
    ratio = await monitor.get_vol_ratio("BTC/USDT")
    assert ratio == 2.0
    mock_redis.set.assert_called_once_with("volatility:BTC/USDT:ratio", "2.0")

@pytest.mark.asyncio
async def test_get_vol_ratio_no_event_returns_one(monitor: VolatilityMonitor, mock_hydra: MagicMock, mock_redis: AsyncMock) -> None:
    mock_hydra.get_latest_event.return_value = None
    
    ratio = await monitor.get_vol_ratio("BTC/USDT")
    assert ratio == 1.0
    mock_redis.set.assert_called_once_with("volatility:BTC/USDT:ratio", "1.0")

@pytest.mark.asyncio
async def test_get_vol_ratio_missing_fields_returns_one(monitor: VolatilityMonitor, mock_hydra: MagicMock, mock_redis: AsyncMock) -> None:
    mock_hydra.get_latest_event.return_value = create_event(vol_1h=None, vol_30d=None)
    
    ratio = await monitor.get_vol_ratio("BTC/USDT")
    assert ratio == 1.0

def test_adaptive_ttl_halved(monitor: VolatilityMonitor) -> None:
    # Test: vol_ratio=2.0 TTL halved
    ttl = monitor.get_adaptive_ttl(100, 2.0)
    assert ttl == 50

def test_adaptive_ttl_clamped_floor(monitor: VolatilityMonitor) -> None:
    # Test: vol_ratio=4.0 TTL clamped at 0.25x base (floor)
    ttl = monitor.get_adaptive_ttl(100, 4.0)
    assert ttl == 25
    ttl_extreme = monitor.get_adaptive_ttl(100, 10.0)
    assert ttl_extreme == 25

def test_adaptive_ttl_ceiling(monitor: VolatilityMonitor) -> None:
    # low volatility extends up to 4.0x
    ttl = monitor.get_adaptive_ttl(100, 0.25)
    assert ttl == 400
    ttl_extreme = monitor.get_adaptive_ttl(100, 0.1)
    assert ttl_extreme == 400

@pytest.mark.asyncio
async def test_lifecycle(monitor: VolatilityMonitor) -> None:
    await monitor.start()
    assert monitor._update_task is not None
    await monitor.close()
    assert monitor._update_task is None
