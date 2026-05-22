import pytest
from unittest.mock import AsyncMock

import httpx
import redis.asyncio as redis_async
from decimal import Decimal

from atlas.providers.hypertracker.provider import HypertrackerProvider
from atlas.providers.hypertracker.models import HypertrackerSnapshot


@pytest.fixture
def mock_redis():
    return AsyncMock(spec=redis_async.Redis)


@pytest.fixture
def mock_http():
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.mark.asyncio
async def test_hypertracker_graceful_degradation(mock_redis, mock_http):
    """Test that provider degrades gracefully on HTTP error without crashing."""
    mock_http.post.side_effect = httpx.TimeoutException("Timeout")

    provider = HypertrackerProvider(redis_client=mock_redis, http_client=mock_http)
    snapshot = await provider.fetch_data()

    assert isinstance(snapshot, HypertrackerSnapshot)
    assert snapshot.status == "degraded"
    assert snapshot.estimated_liquidation_volume == Decimal("0")
    assert snapshot.hlp_vault_drawdown == Decimal("0")
    assert provider.status == "DEGRADED"


@pytest.mark.asyncio
async def test_hypertracker_successful_fetch(mock_redis, mock_http):
    """Test successful data fetch returning instantiated Pydantic object."""
    mock_resp = AsyncMock(spec=httpx.Response)
    mock_resp.raise_for_status.return_value = None
    mock_resp.read.return_value = b'{"status": "ok"}'
    mock_http.post.return_value = mock_resp

    provider = HypertrackerProvider(redis_client=mock_redis, http_client=mock_http)
    snapshot = await provider.fetch_data()

    assert isinstance(snapshot, HypertrackerSnapshot)
    assert snapshot.status == "healthy"
    assert snapshot.estimated_liquidation_volume == Decimal("0")
    assert snapshot.hlp_vault_drawdown == Decimal("0")
    assert provider.status == "HEALTHY"
