"""Tests for StreamProducer."""

import msgspec
import pytest
from unittest.mock import AsyncMock
from atlas.core.stream_producer import StreamProducer
from atlas.core.stream_payloads import HydraPriceStreamPayload


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.xadd.return_value = b"12345-0"
    return redis


@pytest.mark.asyncio
async def test_stream_producer_publish(mock_redis: AsyncMock) -> None:
    """Test that StreamProducer successfully publishes."""
    producer = StreamProducer(mock_redis)
    data = HydraPriceStreamPayload(
        asset="BTC/USDT",
        price_str="50000.0",
        source_exchange="binance",
        timestamp_iso="2026-04-21T14:30:00+00:00"
    )
    
    msg_id = await producer.publish("test:stream", data)
    
    assert msg_id == "12345-0"
    mock_redis.xadd.assert_called_once()
    args, kwargs = mock_redis.xadd.call_args
    assert args[0] == "test:stream"
    payload = args[1]
    assert "data" in payload
    decoded = msgspec.json.decode(payload["data"])
    assert decoded["price_str"] == "50000.0"
    assert kwargs["maxlen"] == 10_000
    assert kwargs["approximate"] is True


@pytest.mark.asyncio
async def test_stream_producer_handles_error(mock_redis: AsyncMock) -> None:
    """Test that producer raises RuntimeError on failure."""
    mock_redis.xadd.side_effect = Exception("Redis error")
    producer = StreamProducer(mock_redis)
    data = HydraPriceStreamPayload(
        asset="BTC/USDT",
        price_str="50000.0",
        source_exchange="binance",
        timestamp_iso="2026-04-21T14:30:00+00:00"
    )
    
    with pytest.raises(RuntimeError, match="Stream publish failed"):
        await producer.publish("test:stream", data)
