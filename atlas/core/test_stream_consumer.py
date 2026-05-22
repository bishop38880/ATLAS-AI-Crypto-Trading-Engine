"""Tests for StreamConsumer."""

import msgspec
import pytest
from unittest.mock import AsyncMock
import redis.asyncio as redis_async
from atlas.core.stream_consumer import StreamConsumer


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    return redis


@pytest.mark.asyncio
async def test_setup_group_idempotent(mock_redis: AsyncMock) -> None:
    """Test group creation."""
    consumer = StreamConsumer(mock_redis)
    await consumer.setup_group("stream", "group")
    mock_redis.xgroup_create.assert_called_once_with("stream", "group", id="$", mkstream=True)


@pytest.mark.asyncio
async def test_setup_group_ignores_busygroup(mock_redis: AsyncMock) -> None:
    """Test group creation ignores BUSYGROUP."""
    mock_redis.xgroup_create.side_effect = redis_async.ResponseError("BUSYGROUP Consumer Group name already exists")
    consumer = StreamConsumer(mock_redis)
    await consumer.setup_group("stream", "group")
    mock_redis.xgroup_create.assert_called_once()


@pytest.mark.asyncio
async def test_setup_group_raises_other_errors(mock_redis: AsyncMock) -> None:
    """Test group creation raises other ResponseErrors."""
    mock_redis.xgroup_create.side_effect = redis_async.ResponseError("SOME ERROR")
    consumer = StreamConsumer(mock_redis)
    with pytest.raises(redis_async.ResponseError):
        await consumer.setup_group("stream", "group")


@pytest.mark.asyncio
async def test_consume_messages(mock_redis: AsyncMock) -> None:
    """Test consumption without auto-ack."""
    mock_payload = msgspec.json.encode({"val": 1})
    mock_redis.xreadgroup.return_value = [
        (b"test:stream", [(b"1000-0", {b"data": mock_payload})])
    ]
    
    consumer = StreamConsumer(mock_redis)
    messages = await consumer.consume("test:stream", "group", "c1")
    
    assert len(messages) == 1
    assert messages[0].entry_id == "1000-0"
    assert messages[0].payload_bytes == mock_payload
    
    mock_redis.xreadgroup.assert_called_once_with(
        groupname="group",
        consumername="c1",
        streams={"test:stream": ">"},
        count=10,
        block=100
    )


@pytest.mark.asyncio
async def test_consume_latest(mock_redis: AsyncMock) -> None:
    """Test consuming the latest message."""
    mock_payload = msgspec.json.encode({"val": 2})
    mock_redis.xrevrange.return_value = [
        (b"2000-0", {b"data": mock_payload})
    ]
    
    consumer = StreamConsumer(mock_redis)
    message = await consumer.consume_latest("test:stream")
    
    assert message is not None
    assert message.entry_id == "2000-0"
    assert message.payload_bytes == mock_payload
    
    mock_redis.xrevrange.assert_called_once_with("test:stream", max="+", min="-", count=1)


@pytest.mark.asyncio
async def test_ack(mock_redis: AsyncMock) -> None:
    """Test manual acknowledgment."""
    consumer = StreamConsumer(mock_redis)
    await consumer.ack("test:stream", "group", "1000-0")
    mock_redis.xack.assert_called_once_with("test:stream", "group", "1000-0")
