"""Tests for OnChainAgent — Nansen Smart Money & Exchange Flow scoring.

Adheres to Sentinel v3.0 architectural invariants.
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import ValidationError

from atlas.agents.onchain.onchain_agent import OnChainAgent
from atlas.providers.nansen.models import SmartMoneyFlow, ExchangeNetflow, NansenSnapshot


def _snapshot(
    sm_netflow: str = "0",
    ex_netflow: str = "0",
    unique_wallets: int = 20,
    asset: str = "ETH",
) -> NansenSnapshot:
    """Helper to create a NansenSnapshot for testing."""
    sm_24h = SmartMoneyFlow(
        asset=asset,
        chain="ethereum",
        net_flow_usd=Decimal(sm_netflow),
        unique_smart_wallets=unique_wallets
    )
    ex_flow = ExchangeNetflow(
        asset=asset,
        netflow_usd=Decimal(ex_netflow)
    )
    return NansenSnapshot(
        asset=asset,
        smart_money_flow_24h=sm_24h,
        smart_money_flow_7d=sm_24h,
        exchange_netflow=ex_flow,
        fetched_at=datetime.now(timezone.utc)
    )


@pytest.mark.asyncio
async def test_onchain_max_score_accumulation() -> None:
    """Full accumulation: scaled to 39-pt on-chain pillar (7/7 * 39)."""
    agent = OnChainAgent()
    # SM In ($5M), EX Out (-$10M), 20 wallets
    snap = _snapshot(sm_netflow="5000000", ex_netflow="-10000000", unique_wallets=20)
    result = await agent.score(data={"asset": "ETH"}, context={"nansen_snapshot": snap})

    # Base SM (4) + EX Out (3) = 7
    assert result.score == 39


@pytest.mark.asyncio
async def test_onchain_low_confidence_halving() -> None:
    """Low confidence (< 10 wallets) should halve the Smart Money score."""
    agent = OnChainAgent()
    # SM In ($5M), EX Neutral, 5 wallets
    # Base SM = 4, Confidence 0.5 -> 2 pts
    snap = _snapshot(sm_netflow="5000000", ex_netflow="0", unique_wallets=5)
    result = await agent.score(data={"asset": "ETH"}, context={"nansen_snapshot": snap})

    # SM In ($5M) @ 0.5 conf → 2 raw; exchange neutral → 0; scaled 2/7 * 39 ≈ 11
    assert result.score == int(round(2.0 / 7.0 * 39.0))
    assert "LOW_CONFIDENCE" in result.explanation


@pytest.mark.asyncio
async def test_onchain_exchange_outflow_only() -> None:
    """Exchange outflow alone — 3 raw pts scaled to 39-pt pillar ≈ 17."""
    agent = OnChainAgent()
    # SM Neutral, EX Out (-$10M)
    snap = _snapshot(sm_netflow="0", ex_netflow="-10000000")
    result = await agent.score(data={"asset": "ETH"}, context={"nansen_snapshot": snap})

    assert result.score == int(round(3.0 / 7.0 * 39.0))


@pytest.mark.asyncio
async def test_onchain_distribution_returns_zero() -> None:
    """Smart money distribution (negative netflow) should return 0 (bullish only agent)."""
    agent = OnChainAgent()
    snap = _snapshot(sm_netflow="-5000000", ex_netflow="0")
    result = await agent.score(data={"asset": "ETH"}, context={"nansen_snapshot": snap})

    assert result.score == 0


@pytest.mark.asyncio
async def test_onchain_empty_context() -> None:
    """Missing snapshot should produce zero score and no crash."""
    agent = OnChainAgent()
    result = await agent.score(data={"asset": "ETH"}, context={})

    assert result.score == 0


@pytest.mark.asyncio
async def test_onchain_result_is_frozen() -> None:
    """Verify that OnChainAgent returns a frozen AgentResult."""
    agent = OnChainAgent()
    snap = _snapshot(sm_netflow="1000000")
    result = await agent.score(data={"asset": "ETH"}, context={"nansen_snapshot": snap})

    # Pydantic v2 raises ValidationError (frozen_instance) on assignment
    with pytest.raises(ValidationError):
        result.score = 100  # type: ignore
