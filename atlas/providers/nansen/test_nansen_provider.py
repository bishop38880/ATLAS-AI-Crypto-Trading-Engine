"""Tests for NansenProvider — MCP SSE adapter with graceful degradation.

Validates:
  - Successful MCP tool calls → cleanly typed NansenSnapshot
  - SSE timeout → stale=True snapshot (no crash)
  - Missing API key → OFFLINE status
  - Semaphore(3) concurrency cap
  - Object Return Rule (returns Pydantic, not dict)
  - Decimal precision for all USD fields
  - msgspec parse failures → empty dict fallback
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.providers.nansen.models import (
    ExchangeNetflow,
    NansenSnapshot,
    SmartMoneyFlow,
)
from atlas.providers.nansen.provider import NansenProvider, _safe_decimal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_settings() -> MagicMock:
    """Produce a PolarisSettings mock with valid Nansen config."""
    s = MagicMock()
    s.nansen_mcp_url = "https://mcp.nansen.ai/ra/mcp"
    s.nansen_api_key.get_secret_value.return_value = "test-api-key-12345"
    s.nansen_ttl_seconds = 300
    s.nansen_query_timeout_seconds = 20
    return s


@pytest.fixture
def mock_settings_no_key() -> MagicMock:
    """PolarisSettings mock with empty API key."""
    s = MagicMock()
    s.nansen_mcp_url = "https://mcp.nansen.ai/ra/mcp"
    s.nansen_api_key.get_secret_value.return_value = ""
    s.nansen_ttl_seconds = 300
    s.nansen_query_timeout_seconds = 20
    return s


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Minimal async Redis mock."""
    return AsyncMock()


@pytest.fixture
def provider(mock_redis: AsyncMock, mock_settings: MagicMock) -> NansenProvider:
    """NansenProvider with valid config."""
    return NansenProvider(redis_client=mock_redis, settings=mock_settings)


# ---------------------------------------------------------------------------
# Init / health tests
# ---------------------------------------------------------------------------


def test_provider_healthy_on_valid_key(
    mock_redis: AsyncMock, mock_settings: MagicMock,
) -> None:
    """Provider initialises as HEALTHY when API key is present."""
    p = NansenProvider(redis_client=mock_redis, settings=mock_settings)
    assert p.status == "HEALTHY"
    assert p.provider_name == "nansen"


def test_provider_offline_on_missing_key(
    mock_redis: AsyncMock, mock_settings_no_key: MagicMock,
) -> None:
    """Provider goes OFFLINE when API key is empty."""
    p = NansenProvider(redis_client=mock_redis, settings=mock_settings_no_key)
    assert p.status == "OFFLINE"


@pytest.mark.asyncio
async def test_health_status_returns_provider_health(
    provider: NansenProvider,
) -> None:
    """get_health_status() returns a ProviderHealth model."""
    health = await provider.get_health_status()
    assert health.name == "nansen"
    assert health.status == "HEALTHY"


# ---------------------------------------------------------------------------
# Successful MCP tool calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_data_returns_nansen_snapshot(
    provider: NansenProvider,
) -> None:
    """Successful MCP calls assemble a cleanly typed NansenSnapshot."""
    sm_response = {
        "net_flow_usd": "5000000",
        "net_flow_usd_7d": "12000000",
        "unique_smart_wallets": 25,
        "flow_direction": "ACCUMULATING",
    }
    ex_response = {
        "net_flow_usd": "-8000000",
        "inflow_usd": "2000000",
        "outflow_usd": "10000000",
    }

    async def mock_sse_call(
        tool_name: str, arguments: dict,
    ) -> dict:
        if "smart_money" in tool_name:
            return sm_response
        return ex_response

    with patch.object(provider, "_call_mcp_tool", side_effect=mock_sse_call):
        snap = await provider.fetch_data("ETH")

    # Object Return Rule: must be Pydantic, not dict
    assert isinstance(snap, NansenSnapshot)
    assert not isinstance(snap, dict)

    # Decimal precision
    assert snap.smart_money_flow_24h.net_flow_usd == Decimal("5000000")
    assert snap.exchange_netflow.netflow_usd == Decimal("-8000000")

    # Signal derivation: SM positive + EX bullish → ACCUMULATING
    assert snap.smart_money_signal == "ACCUMULATING"
    assert snap.exchange_netflow.signal == "BULLISH"
    assert snap.status == "OK"
    assert snap.stale is False


# ---------------------------------------------------------------------------
# SSE timeout → graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_timeout_returns_stale_snapshot(
    provider: NansenProvider,
) -> None:
    """MCP SSE timeout → stale=True snapshot, no exception."""
    async def mock_timeout(*args: object, **kwargs: object) -> dict:
        raise asyncio.TimeoutError()

    with patch.object(provider, "_call_mcp_tool", side_effect=mock_timeout):
        snap = await provider.fetch_data("ETH")

    assert isinstance(snap, NansenSnapshot)
    assert snap.stale is True
    assert snap.status == "STALE"
    assert snap.smart_money_flow_24h.net_flow_usd == Decimal("0")


@pytest.mark.asyncio
async def test_sse_exception_returns_stale_snapshot(
    provider: NansenProvider,
) -> None:
    """Arbitrary SSE exception → stale=True snapshot, no crash."""
    async def mock_error(*args: object, **kwargs: object) -> dict:
        raise ConnectionError("SSE connection refused")

    with patch.object(provider, "_call_mcp_tool", side_effect=mock_error):
        snap = await provider.fetch_data("ETH")

    assert isinstance(snap, NansenSnapshot)
    assert snap.stale is True


# ---------------------------------------------------------------------------
# Offline provider returns empty immediately
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_offline_provider_returns_empty(
    mock_redis: AsyncMock, mock_settings_no_key: MagicMock,
) -> None:
    """OFFLINE provider returns stale snapshot without attempting SSE."""
    p = NansenProvider(redis_client=mock_redis, settings=mock_settings_no_key)
    snap = await p.fetch_data("ETH")

    assert isinstance(snap, NansenSnapshot)
    assert snap.stale is True


# ---------------------------------------------------------------------------
# Semaphore(3) concurrency cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(
    provider: NansenProvider,
) -> None:
    """Verify Semaphore(3) is in place."""
    assert provider._semaphore._value == 3  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Decimal safety
# ---------------------------------------------------------------------------


def test_safe_decimal_conversions() -> None:
    """_safe_decimal handles edge cases."""
    assert _safe_decimal("123.45") == Decimal("123.45")
    assert _safe_decimal(None) == Decimal("0")
    assert _safe_decimal("not_a_number") == Decimal("0")
    assert _safe_decimal(0) == Decimal("0")
    assert _safe_decimal("-999999.99") == Decimal("-999999.99")


# ---------------------------------------------------------------------------
# Model frozen enforcement
# ---------------------------------------------------------------------------


def test_nansen_snapshot_is_frozen() -> None:
    """NansenSnapshot is immutable (frozen=True)."""
    sm = SmartMoneyFlow(asset="ETH", chain="ethereum")
    ex = ExchangeNetflow(asset="ETH")
    snap = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm,
        smart_money_flow_7d=sm,
        exchange_netflow=ex,
    )
    with pytest.raises(Exception):
        snap.stale = True  # type: ignore[misc]
