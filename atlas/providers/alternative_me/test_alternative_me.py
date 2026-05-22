"""Test suite for AlternativeMeConnector — Tier 2 macro sentiment.

Covers:
  1. Successful fetch with string-to-int casting.
  2. Degraded snapshot on HTTP 500.
  3. msgspec usage verification.
  4. Redis caching path.
  5. fetch_data() returns Pydantic object.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import msgspec
import pytest

from atlas.providers.alternative_me.connector import AlternativeMeConnector
from atlas.providers.alternative_me.models import AlternativeMeSnapshot, FearAndGreedData


@pytest.fixture
def mock_settings() -> MagicMock:
    """Mock settings with Alternative.me config."""
    s = MagicMock()
    s.alternative_me_base_url = "https://api.alternative.me/fng/"
    s.alternative_me_ttl_seconds = 43200
    return s


@pytest.fixture
def redis_client() -> AsyncMock:
    """Mock Redis client with cache miss."""
    r = AsyncMock()
    r.get.return_value = None
    return r


@pytest.fixture
def mock_http_client() -> AsyncMock:
    """Mock httpx.AsyncClient returning valid F&G data."""
    client = AsyncMock(spec=httpx.AsyncClient)
    
    # Payload matches Alternative.me structure
    # Note: value is a string "40"
    payload = {
        "name": "Fear and Greed Index",
        "data": [
            {
                "value": "40",
                "value_classification": "Fear",
                "timestamp": "1714176000",
                "time_until_update": "12345"
            }
        ],
        "metadata": {"error": None}
    }
    
    resp = Mock(spec=httpx.Response)
    resp.raise_for_status = Mock()
    resp.content = msgspec.json.encode(payload)
    resp.status_code = 200
    
    client.get.return_value = resp
    return client


@pytest.mark.asyncio
async def test_alternative_me_fetch_success(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """Verify successful fetch and string-to-int casting."""
    provider = AlternativeMeConnector(redis_client, mock_settings, mock_http_client)
    snapshot = await provider.fetch_data()

    assert isinstance(snapshot, AlternativeMeSnapshot)
    assert snapshot.status == "HEALTHY"
    assert snapshot.stale is False
    assert snapshot.data is not None
    # CRITICAL: "40" (str) -> 40 (int)
    assert isinstance(snapshot.data.value, int)
    assert snapshot.data.value == 40
    assert snapshot.data.value_classification == "Fear"
    
    # Verify Redis persistence was called
    redis_client.setex.assert_called_once()


@pytest.mark.asyncio
async def test_alternative_me_http_error_degradation(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
) -> None:
    """Verify graceful degradation on HTTP 500."""
    http_client = AsyncMock(spec=httpx.AsyncClient)
    http_client.get.side_effect = httpx.HTTPStatusError(
        "Internal Server Error",
        request=Mock(spec=httpx.Request),
        response=Mock(spec=httpx.Response, status_code=500)
    )

    provider = AlternativeMeConnector(redis_client, mock_settings, http_client)
    snapshot = await provider.fetch_data()

    assert snapshot.status == "UNAVAILABLE"
    assert snapshot.stale is True
    assert snapshot.data is None
    assert snapshot.error is not None
    assert "Internal Server Error" in snapshot.error


@pytest.mark.asyncio
async def test_alternative_me_cache_hit(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
) -> None:
    """Verify cache hit path avoids network call."""
    # Build a cached snapshot
    cached_data = AlternativeMeSnapshot(
        data=FearAndGreedData(value=75, value_classification="Greed", timestamp=12345),
        status="HEALTHY"
    )
    # redis.get returns msgspec encoded bytes
    redis_client.get.return_value = msgspec.json.encode(cached_data.model_dump())
    
    http_client = AsyncMock(spec=httpx.AsyncClient)
    provider = AlternativeMeConnector(redis_client, mock_settings, http_client)
    
    snapshot = await provider.fetch_data()
    
    assert snapshot.data is not None
    assert snapshot.data.value == 75
    assert snapshot.status == "HEALTHY"
    # Ensure no network call was made
    http_client.get.assert_not_called()


@pytest.mark.asyncio
async def test_msgspec_decoding_enforced(
    redis_client: AsyncMock,
    mock_settings: MagicMock,
    mock_http_client: AsyncMock,
) -> None:
    """Verify msgspec is used for decoding (via patch)."""
    provider = AlternativeMeConnector(redis_client, mock_settings, mock_http_client)
    
    with patch("msgspec.json.decode", wraps=msgspec.json.decode) as mock_decode:
        await provider.fetch_data()
        assert mock_decode.called
