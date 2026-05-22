from decimal import Decimal

import msgspec
from unittest.mock import AsyncMock

import pytest

from atlas.signals.outcome import TradeOutcome, ExitReason
from atlas.signals.outcome_route import record_outcome
from fastapi import HTTPException
import asyncpg  # type: ignore[import-untyped]
from redis.asyncio import Redis


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    # Mock the return of fetchrow to simulate a found signal
    pool.fetchrow.return_value = {
        "raw_score": 150,
        "decision": "Buy",
        "reasoning": "Test reasoning"
    }
    return pool


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.write_outcome.return_value = True
    writer.write_pattern.return_value = "pattern-uuid"
    return writer


@pytest.fixture
def mock_redis() -> AsyncMock:
    redis = AsyncMock()
    return redis


@pytest.mark.asyncio
async def test_record_outcome_success(mock_pool: AsyncMock, mock_writer: AsyncMock, mock_redis: AsyncMock) -> None:
    payload = TradeOutcome(
        signal_id="sig-test",
        pnl_pct=Decimal("2.5"),
        exit_reason=ExitReason.TAKE_PROFIT
    )
    response = await record_outcome(
        outcome=payload,
        pool=mock_pool,
        writer=mock_writer,
        redis=mock_redis,
    )
    
    assert response["status"] == "success"
    assert response["classification"] == "GOOD_WIN"
    
    mock_pool.fetchrow.assert_called_once()
    mock_writer.write_outcome.assert_called_once_with(
        signal_id="sig-test",
        pnl_pct=2.5,
        outcome_label="WIN",
        exit_reason="TAKE_PROFIT",
    )
    mock_writer.write_pattern.assert_called_once()
    mock_redis.publish.assert_called_once()
    
    # Check published message
    published_args = mock_redis.publish.call_args[0]
    assert published_args[0] == "atlas:learning_updates"
    payload_str = published_args[1]
    parsed = msgspec.json.decode(payload_str)
    assert parsed["signal_id"] == "sig-test"
    assert parsed["classification"] == "GOOD_WIN"


@pytest.mark.asyncio
async def test_record_outcome_not_found(mock_pool: AsyncMock, mock_writer: AsyncMock, mock_redis: AsyncMock) -> None:
    mock_pool.fetchrow.return_value = None
    payload = TradeOutcome(
        signal_id="sig-unknown",
        pnl_pct=Decimal("-1.0"),
        exit_reason=ExitReason.STOP_LOSS
    )
    with pytest.raises(HTTPException) as excinfo:
        await record_outcome(
            outcome=payload,
            pool=mock_pool,
            writer=mock_writer,
            redis=mock_redis,
        )
    
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Signal not found in history"
