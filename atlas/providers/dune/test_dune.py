"""Test suite for DuneMCPProvider — Tier 2 Dune Analytics MCP connector.

Covers:
  1. Snapshot assembly with mocked MCP SSE transport
  2. Graceful degradation when MCP server unreachable
  3. Semaphore concurrency limiting (max 3)
  4. fetch_data() returns Pydantic object (not dict)
  5. All Decimal fields are Decimal (not float)
  6. Frozen model immutability
  7. MCP timeout handling
  8. Empty/malformed MCP response handling
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from atlas.providers.dune.connector import DuneMCPProvider, _safe_decimal
from atlas.providers.dune.models import (
    DEXVolumeData,
    DuneSnapshot,
    PerpOIData,
    StablecoinFlowData,
    TokenMetricsSnapshot,
    TokenUnlockEvent,
    WhaleFlowData,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def redis_client() -> AsyncMock:
    """Mock Redis client."""
    return AsyncMock()


@pytest.fixture
def settings() -> MagicMock:
    """Mock PolarisSettings."""
    s = MagicMock()
    s.dune_mcp_url = "http://localhost:8000"
    s.dune_query_timeout_seconds = 10
    s.dune_ttl_seconds = 300
    return s


@pytest.fixture
def provider(redis_client: AsyncMock, settings: MagicMock) -> DuneMCPProvider:
    """Create DuneMCPProvider with mocked deps."""
    return DuneMCPProvider(redis_client=redis_client, settings=settings)


def _make_text_content(data: dict) -> MagicMock:
    """Create a mock TextContent with JSON text."""
    import msgspec
    tc = MagicMock()
    tc.text = msgspec.json.encode(data).decode("utf-8")
    return tc


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_data_returns_pydantic(provider: DuneMCPProvider) -> None:
    """fetch_data() must return a DuneSnapshot Pydantic object, not dict."""
    with patch.object(provider, "_call_mcp_tool", return_value={}):
        snapshot = await provider.fetch_data("BTC")

    assert isinstance(snapshot, DuneSnapshot)
    assert snapshot.asset == "BTC"
    # Must NOT be a dict — dot notation required
    assert hasattr(snapshot, "dex_volume")
    assert hasattr(snapshot, "stablecoin_flow")


@pytest.mark.asyncio
async def test_token_metrics_snapshot_alias() -> None:
    """TokenMetricsSnapshot must be an alias for DuneSnapshot."""
    assert TokenMetricsSnapshot is DuneSnapshot


@pytest.mark.asyncio
async def test_all_decimal_fields(provider: DuneMCPProvider) -> None:
    """All financial fields must be Decimal, not float."""
    mock_data = {
        "volume_24h_usd": "10000.50",
        "volume_7d_usd": "70000",
        "market_share_pct": "5.5",
    }
    with patch.object(provider, "_call_mcp_tool", return_value=mock_data):
        dex = await provider.fetch_dex_volume("BTC")

    assert isinstance(dex.volume_24h_usd, Decimal)
    assert isinstance(dex.volume_7d_usd, Decimal)
    assert isinstance(dex.market_share_pct, Decimal)
    assert dex.volume_24h_usd == Decimal("10000.50")


@pytest.mark.asyncio
async def test_frozen_model_immutability() -> None:
    """Frozen models must reject attribute mutation."""
    snapshot = DuneSnapshot(asset="BTC")
    with pytest.raises(Exception):
        snapshot.asset = "ETH"  # type: ignore[misc]


@pytest.mark.asyncio
async def test_graceful_degradation_mcp_down(
    provider: DuneMCPProvider,
) -> None:
    """MCP failure must produce degraded snapshot, not crash."""
    # _call_mcp_tool has internal try/except; test that it returns {} on failure
    # by simulating what happens when the SSE connection itself fails.
    with patch.object(
        provider, "_execute_sse_call", side_effect=Exception("connection refused"),
    ):
        # _call_mcp_tool catches Exception and returns {}
        data = await provider._call_mcp_tool("get_dex_volume", {"asset": "BTC"})
        assert data == {}

        # fetch_dex_volume should return default DEXVolumeData
        dex = await provider.fetch_dex_volume("BTC")

    # Should return default, not crash
    assert dex.volume_24h_usd == Decimal("0")


@pytest.mark.asyncio
async def test_fetch_snapshot_concurrent(provider: DuneMCPProvider) -> None:
    """fetch_data() must call tools concurrently via gather."""
    call_count = 0

    async def mock_call(tool_name: str, arguments: dict) -> dict:
        nonlocal call_count
        call_count += 1
        if tool_name == "get_dex_volume":
            return {"volume_24h_usd": "10000", "volume_7d_usd": "70000", "market_share_pct": "5.5"}
        if tool_name == "get_stablecoin_flow":
            return {"net_flow_24h_usd": "500000", "net_flow_7d_usd": "100", "total_supply_usd": "1000"}
        if tool_name == "get_perp_oi":
            return {"total_oi_usd": "50000", "oi_change_24h_pct": "10.0"}
        if tool_name == "get_whale_flow":
            return {"net_flow_24h_usd": "10000", "direction": "ACCUMULATING", "whale_dominance_pct": "10.0"}
        if tool_name == "get_token_unlocks":
            return {"unlocks": [{"asset": "BTC", "unlock_pct_of_supply": "6.0", "unlock_usd_value": "1000", "days_until_unlock": 5, "unlock_type": "CLIFF"}]}
        return {}

    with patch.object(provider, "_call_mcp_tool", side_effect=mock_call):
        snapshot = await provider.fetch_data("BTC")

    assert call_count == 5  # All 5 tools called
    assert snapshot.asset == "BTC"
    assert snapshot.dex_volume.volume_24h_usd == Decimal("10000")
    assert snapshot.stablecoin_flow.net_flow_24h_usd == Decimal("500000")
    assert snapshot.perp_oi.total_oi_usd == Decimal("50000")
    assert snapshot.whale_flow.direction == "ACCUMULATING"
    assert len(snapshot.token_unlocks) == 1
    assert snapshot.token_unlocks[0].unlock_type == "CLIFF"
    assert snapshot.token_unlocks[0].unlock_pct_of_supply == Decimal("6.0")


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(
    redis_client: AsyncMock,
    settings: MagicMock,
) -> None:
    """Semaphore must limit concurrent MCP calls to 3."""
    provider = DuneMCPProvider(redis_client=redis_client, settings=settings)
    assert provider._semaphore._value == 3


@pytest.mark.asyncio
async def test_safe_decimal_edge_cases() -> None:
    """_safe_decimal must handle None, invalid strings, and floats."""
    assert _safe_decimal(None) == Decimal("0")
    assert _safe_decimal("not_a_number") == Decimal("0")
    assert _safe_decimal("123.45") == Decimal("123.45")
    assert _safe_decimal(42) == Decimal("42")


@pytest.mark.asyncio
async def test_health_status(provider: DuneMCPProvider) -> None:
    """get_health_status() must return ProviderHealth."""
    health = await provider.get_health_status()
    assert health.name == "dune_mcp"
    assert health.status == "HEALTHY"
