"""Tests for StreamDLQ."""

import pytest
from unittest.mock import AsyncMock, patch
from atlas.core.stream_dlq import StreamDLQ
from atlas.shared.config import PolarisSettings


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.mark.asyncio
async def test_dlq_claims_and_acks(mock_redis: AsyncMock) -> None:
    """Test that DLQ correctly claims and acks pending messages."""
    mock_redis.xautoclaim.return_value = (b"0-0", [(b"1000-0", {b"field": b"value"})])
    
    settings = PolarisSettings(dead_letter_threshold_seconds=300)
    
    with patch("atlas.core.stream_dlq.PolarisSettings", return_value=settings):
        dlq = StreamDLQ(mock_redis)
        count = await dlq.process_dead_letters("stream", "group")
        
        assert count == 1
        mock_redis.xautoclaim.assert_called_once_with(
            name="stream",
            groupname="group",
            consumername="dlq_processor",
            min_idle_time=300000,
            start_id="0-0",
            count=100
        )
        mock_redis.xack.assert_called_once_with("stream", "group", "1000-0")


@pytest.mark.asyncio
async def test_dlq_no_messages(mock_redis: AsyncMock) -> None:
    """Test DLQ when no messages are pending."""
    mock_redis.xautoclaim.return_value = (b"0-0", [])
    
    settings = PolarisSettings(dead_letter_threshold_seconds=300)
    
    with patch("atlas.core.stream_dlq.PolarisSettings", return_value=settings):
        dlq = StreamDLQ(mock_redis)
        count = await dlq.process_dead_letters("stream", "group")
        
        assert count == 0
        mock_redis.xautoclaim.assert_called_once()
        mock_redis.xack.assert_not_called()
