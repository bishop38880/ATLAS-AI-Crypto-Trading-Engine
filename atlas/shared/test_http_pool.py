"""Tests for ProviderHttpPool."""

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from atlas.shared.http_pool import ProviderHttpPool


@pytest.fixture
def _mock_health():
    """Mock ProviderHealthTracker to avoid Redis dependency."""
    with patch("atlas.shared.http_pool.ProviderHealthTracker") as mock_cls:
        tracker = mock_cls.return_value
        tracker.get_timeout = AsyncMock(return_value=5.0)
        tracker.record_request = AsyncMock()
        yield tracker


@pytest.mark.asyncio
async def test_pool_reuses_connections(respx_mock: Any, _mock_health: Any) -> None:
    """Verify the pool reuses connections (mock transport)."""
    base_url = "https://mock.example.com"
    respx_mock.get(f"{base_url}/test").respond(200, json={"ok": True})

    async with ProviderHttpPool("test_provider", base_url, AsyncMock()) as pool:
        resp1 = await pool.get("/test")
        resp2 = await pool.get("/test")

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        assert resp1.json() == {"ok": True}


@pytest.mark.asyncio
async def test_pool_timeout_raises(respx_mock: Any, _mock_health: Any) -> None:
    """Verify timeout propagates correctly."""
    base_url = "https://mock.example.com"
    respx_mock.get(f"{base_url}/slow").mock(
        side_effect=httpx.ReadTimeout("Timeout")
    )

    async with ProviderHttpPool("test_provider", base_url, AsyncMock()) as pool:
        with pytest.raises(httpx.ReadTimeout):
            await pool.get("/slow")


@pytest.mark.asyncio
async def test_pool_close(_mock_health: Any) -> None:
    """Verify close() cleanly closes the client."""
    pool = ProviderHttpPool("test_provider", "https://mock.example.com", AsyncMock())
    assert not pool._client.is_closed
    await pool.close()
    assert pool._client.is_closed
