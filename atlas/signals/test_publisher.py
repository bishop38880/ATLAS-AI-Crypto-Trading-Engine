"""Tests for SignalPublisher."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent
from atlas.signals.publisher import SignalPublisher


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.publish.return_value = 2  # 2 subscribers received it
    return redis


@pytest.fixture
def sample_signal() -> SignalOutput:
    return SignalOutput(
        signal_id="sig-001",
        timestamp=datetime.now(timezone.utc),
        decision=SignalDecision.BUY,
        asset="BTC/USDT",
        action=ActionBlock(
            side="buy",
            order_type="limit",
            price=Decimal("50000"),
            stop_loss=Decimal("49000"),
            take_profit=Decimal("52000"),
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        score=80,
        confidence=Decimal("0.8"),
        category_scores=CategoryScores(total=80, correlation=80),
        telemetry=TelemetryEvent(cycle_id="test", cycle_latency_ms=10.0, agent_count=1),
    )


@pytest.mark.asyncio
async def test_publisher_success(
    mock_redis: AsyncMock, sample_signal: SignalOutput,
) -> None:
    """Test successful publish via redis.publish."""
    publisher = SignalPublisher(mock_redis)
    receivers = await publisher.publish(sample_signal)

    assert receivers == 2
    mock_redis.publish.assert_called_once()
    args = mock_redis.publish.call_args[0]
    assert args[0] == "atlas:signals"
    # Verify the payload is msgspec encoded bytes
    assert isinstance(args[1], bytes)
    assert b"sig-001" in args[1]


@pytest.mark.asyncio
async def test_publisher_handles_error(
    mock_redis: AsyncMock, sample_signal: SignalOutput,
) -> None:
    """Test publisher handles redis exceptions gracefully."""
    mock_redis.publish.side_effect = Exception("Redis error")
    publisher = SignalPublisher(mock_redis)

    receivers = await publisher.publish(sample_signal)
    assert receivers == 0
