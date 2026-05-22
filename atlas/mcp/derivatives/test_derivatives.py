"""Unit tests for the Derivatives Context MCP Server.

Uses fakeredis to mock the Redis connection and verify tool logic.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, AsyncGenerator

import msgspec
import pytest
import fakeredis.aioredis
from mcp.server.fastmcp import FastMCP

from atlas.mcp.derivatives.server import (
    get_perp_context,
    get_liquidation_clusters,
    evaluate_squeeze_risk,
)


@pytest.fixture
async def mock_redis() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    """Provide a fake Redis client for testing."""
    client = fakeredis.aioredis.FakeRedis()
    # Mock the global _redis_pool in server.py
    import atlas.mcp.derivatives.server as server_mod
    
    old_pool = server_mod._redis_pool
    server_mod._redis_pool = client
    
    try:
        yield client
    finally:
        server_mod._redis_pool = old_pool
        await client.aclose()


@pytest.mark.asyncio
async def test_get_perp_context(mock_redis: Any) -> None:
    """Verify perp context retrieval and decoding."""
    symbol = "BTC-PERP"
    payload = {
        "symbol": symbol,
        "mark_price": "65000.50",
        "aggregated_oi_usd": "1200000000",
        "annualized_funding_apy": "0.125"
    }
    await mock_redis.set(f"coinalyze:state:{symbol}", msgspec.json.encode(payload))
    
    result = await get_perp_context(symbol)
    
    assert result.symbol == symbol
    assert result.mark_price == Decimal("65000.50")
    assert result.aggregated_oi_usd == Decimal("1200000000")
    assert result.annualized_funding_apy == Decimal("0.125")


@pytest.mark.asyncio
async def test_get_liquidation_clusters(mock_redis: Any) -> None:
    """Verify liquidation cluster retrieval and list decoding."""
    symbol = "ETH-PERP"
    payload = [
        {"price_band": "3450.00", "density_score": "85.5", "side": "long"},
        {"price_band": "3600.00", "density_score": "42.0", "side": "short"}
    ]
    await mock_redis.set("hydra:clusters:ETH", msgspec.json.encode(payload))
    
    result = await get_liquidation_clusters(symbol)
    
    assert len(result) == 2
    assert result[0].price_band == Decimal("3450.00")
    assert result[0].side == "long"
    assert result[1].density_score == Decimal("42.0")


@pytest.mark.asyncio
async def test_evaluate_squeeze_risk_veto(mock_redis: Any) -> None:
    """Verify the Absolute Veto logic for squeeze risk."""
    symbol = "BTC-PERP"
    
    # Case 1: Intending LONG, but LONG cascade is active (Price crashing)
    cascade_payload = {"active": True, "side": "LONG"}
    await mock_redis.set("hydra:cascades:live:BTC", msgspec.json.encode(cascade_payload))
    
    report = await evaluate_squeeze_risk(symbol, "LONG")
    assert report.momentum_veto is True
    assert report.hazard_level == "CRITICAL"
    assert "blocked" in report.rationale.lower()

    # Case 2: Intending SHORT, but SHORT cascade is active (Price spiking)
    cascade_payload = {"active": True, "side": "SHORT"}
    await mock_redis.set("hydra:cascades:live:BTC", msgspec.json.encode(cascade_payload))
    
    report = await evaluate_squeeze_risk(symbol, "SHORT")
    assert report.momentum_veto is True
    assert report.hazard_level == "CRITICAL"


@pytest.mark.asyncio
async def test_evaluate_squeeze_risk_pro_trend(mock_redis: Any) -> None:
    """Verify hazard detection when cascade is in opposite direction."""
    symbol = "BTC-PERP"
    
    # Intending LONG, and SHORT cascade is active (Price spiking UP)
    # This is pro-trend, so hazard is MEDIUM but no veto.
    cascade_payload = {"active": True, "side": "SHORT"}
    await mock_redis.set("hydra:cascades:live:BTC", msgspec.json.encode(cascade_payload))
    
    report = await evaluate_squeeze_risk(symbol, "LONG")
    assert report.momentum_veto is False
    assert report.hazard_level == "MEDIUM"
    assert "pro-trend" in report.rationale.lower()


@pytest.mark.asyncio
async def test_evaluate_squeeze_risk_no_cascade(mock_redis: Any) -> None:
    """Verify low risk when no cascade is active."""
    symbol = "BTC-PERP"
    await mock_redis.delete("hydra:cascades:live:BTC")
    
    report = await evaluate_squeeze_risk(symbol, "LONG")
    assert report.momentum_veto is False
    assert report.hazard_level == "LOW"
    assert report.hydra_alert_active is False
