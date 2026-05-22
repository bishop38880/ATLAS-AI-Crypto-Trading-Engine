"""
Integration and tool tests for Prediction Market MCP server.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Verifies tool aggregation logic, platform fallback, and conviction scores.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp_servers.prediction_market.models import Platform, UnifiedEventProbability
from mcp_servers.prediction_market.server import (
    get_fed_rate_implied_path,
    get_regulatory_risk_matrix,
    search_macro_events,
)


@pytest.fixture
def mock_poly_client():
    """Mock PolymarketClient."""
    with patch("mcp_servers.prediction_market.server._poly_client") as mock:
        mock.search_markets = AsyncMock(return_value=[])
        mock.fetch_order_book = AsyncMock(return_value={"bids": [[0.5, 100]], "asks": [[0.55, 100]]})
        mock.fetch_market_by_id = AsyncMock(return_value={
            "condition_id": "poly1",
            "question": "Will SEC approve ETF?",
            "liquidity": "1000000",
            "volume": "5000000",
            "tokens": [{"outcome": "yes", "token_id": "token1"}]
        })
        yield mock


@pytest.fixture
def mock_kalshi_client():
    """Mock KalshiClient."""
    with patch("mcp_servers.prediction_market.server._kalshi_client") as mock:
        mock.is_available = True
        mock.search_events = AsyncMock(return_value=[])
        mock.fetch_markets_for_event = AsyncMock(return_value=[])
        mock.fetch_order_book = AsyncMock(return_value={
            "orderbook": {"yes": [[50, 100]], "no": [[45, 100]]}
        })
        yield mock


@pytest.mark.asyncio
async def test_search_macro_events_aggregation(mock_poly_client, mock_kalshi_client):
    """Test that search_macro_events aggregates from both platforms."""
    mock_poly_client.search_markets.return_value = [{
        "condition_id": "p1", "question": "Poly Event", "liquidity": "50000", "tokens": [{"outcome": "yes", "token_id": "t1"}]
    }]
    mock_kalshi_client.search_events.return_value = [{"event_ticker": "K1", "title": "Kalshi Event"}]
    mock_kalshi_client.fetch_markets_for_event.return_value = [{
        "ticker": "KM1", "title": "Kalshi Market", "open_interest": 60000
    }]

    result_json = await search_macro_events("test", min_liquidity_usd=1000)
    import json
    results = json.loads(result_json)

    assert len(results) >= 2
    platforms = {r["platform"] for r in results}
    assert "polymarket" in platforms
    assert "kalshi" in platforms


@pytest.mark.asyncio
async def test_get_fed_rate_implied_path(mock_poly_client, mock_kalshi_client):
    """Test fed rate path construction."""
    mock_poly_client.search_markets.return_value = [{
        "condition_id": "f1", "question": "Will Fed cut rates in June?", "liquidity": "1000000", 
        "tokens": [{"outcome": "yes", "token_id": "t1"}], "end_date_iso": "2024-06-15"
    }]
    
    result_json = await get_fed_rate_implied_path()
    import json
    result = json.loads(result_json)

    assert "meetings" in result
    assert len(result["meetings"]) > 0
    assert result["meetings"][0]["meeting_date"] == "2024-06-15"


@pytest.mark.asyncio
async def test_get_regulatory_risk_matrix(mock_poly_client, mock_kalshi_client):
    """Test regulatory risk matrix aggregation."""
    mock_poly_client.search_markets.return_value = [{
        "condition_id": "r1", "question": "SEC vs Coinbase ruling", "liquidity": "200000",
        "tokens": [{"outcome": "yes", "token_id": "t1"}]
    }]

    result_json = await get_regulatory_risk_matrix()
    import json
    result = json.loads(result_json)

    assert "events" in result
    assert "net_regulatory_bias" in result
    assert any(e["agency"] == "SEC" for e in result["events"])
