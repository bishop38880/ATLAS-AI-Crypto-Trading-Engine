"""Tests for Nansen provider and OnChain agent integration.

Covers models, connector logic, and agent scoring.
"""

from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, patch

from atlas.providers.nansen.models import (
    ExchangeNetflow,
    NansenSnapshot,
    SmartMoneyFlow,
)
from atlas.providers.nansen.connector import NansenProvider
from atlas.agents.onchain.onchain_agent import OnChainAgent
from atlas.agents.base import SignalDirection


def test_nansen_models_signal_derivation():
    """Test deterministic signal derivation in Nansen models."""
    # Bullish case
    ex_flow = ExchangeNetflow(asset="ETH", netflow_usd=Decimal("-2000000"))
    assert ex_flow.signal == "BULLISH"
    
    sm_flow = SmartMoneyFlow(asset="ETH", chain="ethereum", net_flow_usd=Decimal("500000"))
    
    snap = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm_flow,
        smart_money_flow_7d=sm_flow,
        exchange_netflow=ex_flow,
    )
    assert snap.smart_money_signal == "ACCUMULATING"
    
    # Bearish case
    ex_flow_bear = ExchangeNetflow(asset="ETH", netflow_usd=Decimal("2000000"))
    sm_flow_bear = SmartMoneyFlow(asset="ETH", chain="ethereum", net_flow_usd=Decimal("-500000"))
    snap_bear = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm_flow_bear,
        smart_money_flow_7d=sm_flow_bear,
        exchange_netflow=ex_flow_bear,
    )
    assert snap_bear.smart_money_signal == "DISTRIBUTING"


def test_nansen_confidence_modifier():
    """Test confidence modifier based on unique wallet count."""
    sm_low = SmartMoneyFlow(asset="ETH", chain="ethereum", unique_smart_wallets=5)
    ex = ExchangeNetflow(asset="ETH")
    snap = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm_low,
        smart_money_flow_7d=sm_low,
        exchange_netflow=ex,
    )
    assert snap.confidence_modifier == 0.5
    
    sm_high = SmartMoneyFlow(asset="ETH", chain="ethereum", unique_smart_wallets=15)
    snap_high = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm_high,
        smart_money_flow_7d=sm_high,
        exchange_netflow=ex,
    )
    assert snap_high.confidence_modifier == 1.0


@pytest.mark.asyncio
async def test_nansen_provider_status():
    """Test provider status based on API key presence."""
    p_ok = NansenProvider(mcp_url="http://mock", api_key="test-key")
    assert p_ok.status == "HEALTHY"
    
    p_fail = NansenProvider(mcp_url="http://mock", api_key=None)
    assert p_fail.status == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_nansen_connector_fetch_snapshot():
    """Test snapshot aggregation in NansenProvider."""
    provider = NansenProvider(mcp_url="http://mock", api_key="test-key")
    
    # Mock _call_mcp to return valid tool data
    with patch.object(provider, "_call_mcp", new_callable=AsyncMock) as mock_call:
        mock_call.side_effect = [
            {"net_flow_usd": "100000", "unique_smart_wallets": 12},  # 24h flow
            {"net_flow_usd": "700000", "unique_smart_wallets": 15},  # 7d flow
            {"net_flow_usd": "-5000000", "inflow_usd": "1m", "outflow_usd": "6m"},  # exchange
        ]
        
        snap = await provider.fetch_snapshot("ETH")
        assert snap.asset == "ETH"
        assert snap.smart_money_flow_24h.net_flow_usd == Decimal("100000")
        assert snap.exchange_netflow.signal == "BULLISH"
        assert snap.smart_money_signal == "ACCUMULATING"


@pytest.mark.asyncio
async def test_onchain_agent_scoring():
    """Test OnChainAgent scoring with Nansen context."""
    agent = OnChainAgent()
    
    # Create a bullish snapshot
    sm = SmartMoneyFlow(asset="ETH", chain="ethereum", net_flow_usd=Decimal("1000000"), unique_smart_wallets=20)
    ex = ExchangeNetflow(asset="ETH", netflow_usd=Decimal("-5000000"))
    snap = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm,
        smart_money_flow_7d=sm,
        exchange_netflow=ex,
    )
    
    context = {"nansen_snapshot": snap}
    result = await agent.score(data={}, context=context)
    
    # Raw 4+3=7 scaled to 39-pt on-chain slice (same ratio as confluence pillar)
    assert result.score == 39
    assert result.direction == SignalDirection.BULLISH
    assert "Smart Money accumulation detected" in result.convergences[0]
    assert "Net exchange outflow detected" in result.convergences[1]


@pytest.mark.asyncio
async def test_onchain_agent_confidence_scaling():
    """Test that SM points scale with confidence modifier."""
    agent = OnChainAgent()
    
    # Low confidence snapshot
    sm = SmartMoneyFlow(asset="ETH", chain="ethereum", net_flow_usd=Decimal("1000000"), unique_smart_wallets=5)
    ex = ExchangeNetflow(asset="ETH", netflow_usd=Decimal("-5000000"))
    snap = NansenSnapshot(
        asset="ETH",
        smart_money_flow_24h=sm,
        smart_money_flow_7d=sm,
        exchange_netflow=ex,
    )
    
    context = {"nansen_snapshot": snap}
    result = await agent.score(data={}, context=context)
    
    # Raw int(4*0.5)+3 = 5 → scaled to MAX_SCORE 39
    assert result.score == int(round(5.0 / 7.0 * 39.0))


@pytest.mark.asyncio
async def test_onchain_agent_missing_data():
    """Test agent graceful degradation on missing data."""
    agent = OnChainAgent()
    result = await agent.score(data={}, context={})
    
    assert result.score == 0
    assert result.direction == SignalDirection.NEUTRAL
    assert "DATA_UNAVAILABLE" in result.risks


def test_nansen_to_decimal_safety():
    """Test safety of decimal conversion in provider."""
    p = NansenProvider(mcp_url="http://mock", api_key="test")
    assert p._to_decimal("123.45") == Decimal("123.45")
    assert p._to_decimal(None) == Decimal("0")
    assert p._to_decimal("invalid") == Decimal("0")
