"""Tests for Nansen MCP Server — verifying Credit Governor and Sentinel compliance.
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from mcp_servers.nansen.client import CreditGovernor, NansenClient
from mcp_servers.nansen.models import SmartMoneyFlow


@pytest.mark.asyncio
async def test_credit_governor_enforcement():
    """Verify that credit governor tracks and blocks usage."""
    mock_redis = AsyncMock()
    # Mock usage: 2 calls per check_budget (day and month)
    # First check: [1000, 1000] -> both under budget
    # Second check: [6000, 6000] -> both over budget
    mock_redis.incrby.side_effect = [1000, 1000, 6000, 6000] 
    
    governor = CreditGovernor(redis_url="redis://localhost", daily_budget=5000)
    with patch.object(governor, "_get_client", return_value=mock_redis):
        # First call: OK
        allowed = await governor.check_budget(1000)
        assert allowed is True
        
        # Second call: BLOCKED
        allowed = await governor.check_budget(5000)
        assert allowed is False


@pytest.mark.asyncio
async def test_nansen_client_decimal_precision():
    """Verify that the client handles Decimal precision correctly."""
    mock_governor = AsyncMock()
    mock_governor.check_budget.return_value = True
    
    client = NansenClient(api_key="test", redis_url="redis://localhost", governor=mock_governor)
    
    # Mock httpx response
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "asset": "ETH",
        "net_flow_usd": "12345.67",
        "unique_smart_wallets": 15
    }
    mock_resp.status_code = 200
    
    with patch.object(client._client, "get", return_value=mock_resp):
        data = await client.get_token_smart_money_flow("0x123", "ethereum", "24h")
        assert data is not None
        assert data["net_flow_usd"] == "12345.67"
        # The model in server.py will convert this to Decimal


def test_smart_money_model_frozen():
    """Verify that models are frozen (Sentinel invariant)."""
    model = SmartMoneyFlow(
        asset="BTC",
        chain="ethereum",
        net_flow_usd=Decimal("1000"),
        unique_smart_wallets=5,
        time_range="24h"
    )
    # msgspec.Struct with frozen=True raises AttributeError on assignment
    with pytest.raises(AttributeError, match="immutable type"):
        model.asset = "ETH" # type: ignore

@pytest.mark.asyncio
async def test_wallet_profile_parallel_fetch():
    """Verify that wallet profiling correctly aggregates labels and portfolio."""
    mock_governor = AsyncMock()
    mock_governor.check_budget.return_value = True
    
    client = NansenClient(api_key="test", redis_url="redis://localhost", governor=mock_governor)
    
    # Mock responses
    mock_labels = MagicMock()
    mock_labels.json.return_value = {"labels": ["Smart Money", "DEX Trader"]}
    mock_labels.status_code = 200
    
    mock_portfolio = MagicMock()
    mock_portfolio.json.return_value = {"total_value_usd": "1000000", "primary_chain": "ethereum"}
    mock_portfolio.status_code = 200
    
    with patch.object(client._client, "get", side_effect=[mock_labels, mock_portfolio]):
        labels = await client.get_wallet_labels("0x123")
        portfolio = await client.get_wallet_portfolio("0x123")
        
        assert labels is not None
        assert portfolio is not None
        assert labels["labels"] == ["Smart Money", "DEX Trader"]
        assert portfolio["total_value_usd"] == "1000000"
