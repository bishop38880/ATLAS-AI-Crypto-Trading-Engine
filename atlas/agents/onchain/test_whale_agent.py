"""Tests for WhaleAgent — SOL/JUP ecosystem routing + baseline scoring."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from atlas.agents.onchain.whale_agent import WhaleAgent
from atlas.providers.nansen.models import FlowEntity, NansenSnapshot, RiskIndicators, SmartMoneyNetflow
from atlas.providers.nansen.schemas import BridgeFlowPayload, LSTFlowPayload, RetailFlowPayload


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _snapshot(
    net_flow: str = "0",
    inflow: str = "0",
    outflow: str = "0",
    asset: str = "BTC",
) -> NansenSnapshot:
    return NansenSnapshot(
        asset=asset,
        whale_flows=FlowEntity(
            net_flow_usd=Decimal(net_flow),
            inflow_usd=Decimal(inflow),
            outflow_usd=Decimal(outflow),
        ),
        risk_indicators=RiskIndicators(),
        exchange_flows=FlowEntity(),
        smart_money_flows=FlowEntity(),
        smart_money_dex_activity=SmartMoneyNetflow(),
        fetched_at=datetime.now(timezone.utc),
    )


def _ctx(snap: NansenSnapshot) -> dict[str, Any]:
    return {"nansen_snapshot": snap}


# ---------------------------------------------------------------------------
# Baseline scoring tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_massive_accumulation_max_score() -> None:
    """Whale netflow >$10M + heavy bid bias — full score capped at 26-pt pillar budget."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="15000000", inflow="2000000", outflow="100000")
    result = await agent.score(data={"asset": "BTC"}, context=_ctx(snap))

    # Raw total can land at 25 or 26 depending on tie-breaks; pillar cap is 26
    assert result.score >= 25
    assert result.score <= 26
    assert result.sub_signals["whale_netflow"].flag == "MASSIVE_ACCUMULATION"
    assert result.sub_signals["whale_buy_pressure"].flag == "HEAVY_BID_BIAS"


@pytest.mark.asyncio
async def test_empty_context_returns_zero() -> None:
    """Missing Nansen snapshot should produce safe zero result."""
    agent = WhaleAgent()
    result = await agent.score(data={"asset": "BTC"}, context=None)

    assert result.score == 0
    assert "WHALE_DATA_MISSING" in result.risks


@pytest.mark.asyncio
async def test_distribution_bearish() -> None:
    """Massive distribution triggers bearish direction."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="-15000000", inflow="100000", outflow="200000")
    result = await agent.score(data={"asset": "ETH"}, context=_ctx(snap))

    assert result.score == 0
    assert result.sub_signals["whale_netflow"].flag == "MASSIVE_DISTRIBUTION"


# ---------------------------------------------------------------------------
# SOL ecosystem — LST velocity scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sol_lst_accumulation_adds_points() -> None:
    """SOL + heavy LST inflow should add 15 pts and inject solana_lst_velocity."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="SOL")
    lst_flows = [
        LSTFlowPayload(
            asset="SOL", contract_name="Jito",
            netflow_24h=Decimal("30000"), netflow_7d=Decimal("100000"),
            timestamp=1700000000,
        ),
        LSTFlowPayload(
            asset="SOL", contract_name="Marinade",
            netflow_24h=Decimal("25000"), netflow_7d=Decimal("80000"),
            timestamp=1700000000,
        ),
    ]

    result = await agent.score(
        data={"asset": "SOL", "lst_flows": lst_flows},
        context=_ctx(snap),
    )

    # Strong SOL flow + LST; capped at 26-pt whale pillar budget
    assert result.score == 26
    assert "solana_lst_velocity" in result.sub_signals
    assert result.sub_signals["solana_lst_velocity"].flag == "LST_ACCUMULATION"


@pytest.mark.asyncio
async def test_sol_lst_capitulation_subtracts_points() -> None:
    """SOL + heavy LST outflow should subtract 20 pts and flag capitulation."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="SOL")
    lst_flows = [
        LSTFlowPayload(
            asset="SOL", contract_name="Jito",
            netflow_24h=Decimal("-60000"), netflow_7d=Decimal("-200000"),
            timestamp=1700000000,
        ),
    ]

    result = await agent.score(
        data={"asset": "SOL", "lst_flows": lst_flows},
        context=_ctx(snap),
    )

    # Baseline: 8 + 10 = 18, - 20 LST = -2 → floored at 0
    assert result.score == 0
    assert "solana_lst_velocity" in result.sub_signals
    assert result.sub_signals["solana_lst_velocity"].flag == "LST_CAPITULATION_WARNING"


@pytest.mark.asyncio
async def test_sol_lst_neutral_no_change() -> None:
    """SOL + neutral LST flows should not alter the baseline score."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="SOL")
    lst_flows = [
        LSTFlowPayload(
            asset="SOL", contract_name="Jito",
            netflow_24h=Decimal("100"), netflow_7d=Decimal("500"),
            timestamp=1700000000,
        ),
    ]

    result = await agent.score(
        data={"asset": "SOL", "lst_flows": lst_flows},
        context=_ctx(snap),
    )

    # Baseline: 8 + 10 = 18, + 0 LST = 18
    assert result.score == 18
    assert result.sub_signals["solana_lst_velocity"].flag == "LST_NEUTRAL"


# ---------------------------------------------------------------------------
# JUP ecosystem — Router Dominance scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jup_high_routing_adds_points() -> None:
    """JUP + high dominance pct should add 10 pts and inject jup_router_dominance."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="JUP")

    result = await agent.score(
        data={"asset": "JUP", "jup_dominance_pct": Decimal("90.0")},
        context=_ctx(snap),
    )

    # Baseline: 8 + 10 = 18, + 10 JUP → capped at 26-pt whale budget
    assert result.score == 26
    assert "jup_router_dominance" in result.sub_signals
    assert result.sub_signals["jup_router_dominance"].flag == "SMART_MONEY_ROUTING_PREFERENCE_HIGH"


@pytest.mark.asyncio
async def test_jup_low_routing_subtracts_points() -> None:
    """JUP + low dominance pct should subtract 15 pts and flag decay."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="JUP")

    result = await agent.score(
        data={"asset": "JUP", "jup_dominance_pct": Decimal("45.0")},
        context=_ctx(snap),
    )

    # Baseline: 8 + 10 = 18, - 15 JUP = 3
    assert result.score == 3
    assert "jup_router_dominance" in result.sub_signals
    assert result.sub_signals["jup_router_dominance"].flag == "JUP_MARKETSHARE_DECAY"


@pytest.mark.asyncio
async def test_jup_missing_dominance_no_crash() -> None:
    """JUP without jup_dominance_pct should not crash — fallback to baseline."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000", asset="JUP")

    result = await agent.score(
        data={"asset": "JUP"},
        context=_ctx(snap),
    )

    assert result.score == 18
    assert "jup_router_dominance" not in result.sub_signals


# ---------------------------------------------------------------------------
# Standard EVM asset — no ecosystem side-effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evm_asset_no_lst_crash() -> None:
    """Standard EVM asset should not crash when no LST data present."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="5000000", inflow="300000", outflow="200000", asset="ETH")

    result = await agent.score(
        data={"asset": "ETH"},
        context=_ctx(snap),
    )

    assert "solana_lst_velocity" not in result.sub_signals
    assert "jup_router_dominance" not in result.sub_signals
    assert result.score >= 0


@pytest.mark.asyncio
async def test_result_is_frozen() -> None:
    """AgentResult should be immutable (frozen model)."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000")
    result = await agent.score(data={"asset": "BTC"}, context=_ctx(snap))

    with pytest.raises(Exception):
        result.score = 999  # type: ignore[misc]


@pytest.mark.asyncio
async def test_no_float_in_thresholds() -> None:
    """Verify Decimal enforcement: _eval_solana_lst and _eval_jup_routing use Decimal."""
    agent = WhaleAgent()

    # LST: exact boundary
    _, sig = agent._eval_solana_lst([
        LSTFlowPayload(
            asset="SOL", contract_name="Jito",
            netflow_24h=Decimal("-50000"), netflow_7d=Decimal("0"),
            timestamp=0,
        ),
    ])
    assert sig.flag == "LST_CAPITULATION_WARNING"

    # JUP: exact boundary (85.0 is NOT > 85.0, so should be neutral)
    delta, sig = agent._eval_jup_routing(Decimal("85.0"))
    assert delta == 0
    assert sig.flag == "JUP_ROUTING_NEUTRAL"


# ---------------------------------------------------------------------------
# Retail Divergence scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exit_liquidity_trap_severely_penalises_score() -> None:
    """EXIT_LIQUIDITY_TRAP: smart money out + retail in must deduct 15 pts.

    This is the NON-NEGOTIABLE quality gate test: proves the agent's total
    score is severely penalised when retail acts as exit liquidity.
    """
    agent = WhaleAgent()
    snap = NansenSnapshot(
        asset="SOL",
        whale_flows=FlowEntity(
            net_flow_usd=Decimal("1500000"),
            inflow_usd=Decimal("800000"),
            outflow_usd=Decimal("100000"),
        ),
        risk_indicators=RiskIndicators(),
        exchange_flows=FlowEntity(),
        smart_money_flows=FlowEntity(),
        smart_money_dex_activity=SmartMoneyNetflow(
            net_flow_usd=Decimal("-5000000"),  # SM selling
        ),
        fetched_at=datetime.now(timezone.utc),
    )
    retail = RetailFlowPayload(
        netflow_usd=Decimal("3000000"),  # Retail buying
        buy_pressure_ratio=2.5,
    )

    result = await agent.score(
        data={"asset": "SOL", "retail_flows": retail},
        context={"nansen_snapshot": snap},
    )

    # Baseline: 8 (moderate acc) + 10 (heavy bid) = 18
    # Retail divergence: -15 (EXIT_LIQUIDITY_TRAP)
    # Total: 3 → proves severe penalty
    assert result.score <= 3
    assert "retail_divergence" in result.sub_signals
    assert result.sub_signals["retail_divergence"].flag == "EXIT_LIQUIDITY_TRAP"
    assert "Retail buying into smart money distribution" in result.risks[-1]


@pytest.mark.asyncio
async def test_smart_money_accumulation_adds_points() -> None:
    """SMART_MONEY_ACCUMULATION: SM buying + retail selling should add 10 pts."""
    agent = WhaleAgent()
    snap = NansenSnapshot(
        asset="BTC",
        whale_flows=FlowEntity(
            net_flow_usd=Decimal("1500000"),
            inflow_usd=Decimal("800000"),
            outflow_usd=Decimal("100000"),
        ),
        risk_indicators=RiskIndicators(),
        exchange_flows=FlowEntity(),
        smart_money_flows=FlowEntity(),
        smart_money_dex_activity=SmartMoneyNetflow(
            net_flow_usd=Decimal("5000000"),  # SM buying
        ),
        fetched_at=datetime.now(timezone.utc),
    )
    retail = RetailFlowPayload(
        netflow_usd=Decimal("-2000000"),  # Retail selling
        buy_pressure_ratio=0.4,
    )

    result = await agent.score(
        data={"asset": "BTC", "retail_flows": retail},
        context={"nansen_snapshot": snap},
    )

    # Baseline: 8 + 10 = 18, + 10 (SMART_MONEY_ACCUMULATION) = 28 → capped at MAX_SCORE 26
    assert result.score == 26
    assert result.sub_signals["retail_divergence"].flag == "SMART_MONEY_ACCUMULATION"


# ---------------------------------------------------------------------------
# Bridge Rotation scoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_massive_inflow_adds_points() -> None:
    """CROSS_CHAIN_CAPITAL_INFLOW: >$10M bridge inflow should add 10 pts."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000")
    bridges = [
        BridgeFlowPayload(
            bridge_name="Wormhole",
            net_inflow_usd=Decimal("8000000"),
            target_chain="solana",
        ),
        BridgeFlowPayload(
            bridge_name="Portal",
            net_inflow_usd=Decimal("5000000"),
            target_chain="solana",
        ),
    ]

    result = await agent.score(
        data={"asset": "BTC", "bridge_flows": bridges},
        context=_ctx(snap),
    )

    # Baseline: 8 + 10 = 18, + 10 (bridge) = 28 → capped at MAX_SCORE 26
    assert result.score == 26
    assert "bridge_rotation" in result.sub_signals
    assert result.sub_signals["bridge_rotation"].flag == "CROSS_CHAIN_CAPITAL_INFLOW"


@pytest.mark.asyncio
async def test_bridge_below_threshold_neutral() -> None:
    """Bridge inflow below $10M should not add points."""
    agent = WhaleAgent()
    snap = _snapshot(net_flow="1500000", inflow="800000", outflow="100000")
    bridges = [
        BridgeFlowPayload(
            bridge_name="Wormhole",
            net_inflow_usd=Decimal("3000000"),
            target_chain="solana",
        ),
    ]

    result = await agent.score(
        data={"asset": "BTC", "bridge_flows": bridges},
        context=_ctx(snap),
    )

    assert result.score == 18
    assert result.sub_signals["bridge_rotation"].flag == "BRIDGE_FLOW_NEUTRAL"


@pytest.mark.asyncio
async def test_combined_retail_trap_and_bridge_inflow() -> None:
    """Combined: EXIT_LIQUIDITY_TRAP (-15) + CROSS_CHAIN_CAPITAL_INFLOW (+10)."""
    agent = WhaleAgent()
    snap = NansenSnapshot(
        asset="BTC",
        whale_flows=FlowEntity(
            net_flow_usd=Decimal("1500000"),
            inflow_usd=Decimal("800000"),
            outflow_usd=Decimal("100000"),
        ),
        risk_indicators=RiskIndicators(),
        exchange_flows=FlowEntity(),
        smart_money_flows=FlowEntity(),
        smart_money_dex_activity=SmartMoneyNetflow(
            net_flow_usd=Decimal("-5000000"),
        ),
        fetched_at=datetime.now(timezone.utc),
    )
    retail = RetailFlowPayload(
        netflow_usd=Decimal("3000000"),
        buy_pressure_ratio=2.5,
    )
    bridges = [
        BridgeFlowPayload(
            bridge_name="Wormhole",
            net_inflow_usd=Decimal("15000000"),
            target_chain="solana",
        ),
    ]

    result = await agent.score(
        data={
            "asset": "BTC",
            "retail_flows": retail,
            "bridge_flows": bridges,
        },
        context={"nansen_snapshot": snap},
    )

    # Baseline: 8 + 10 = 18, - 15 (trap) + 10 (bridge) = 13
    assert result.score == 13
    assert result.sub_signals["retail_divergence"].flag == "EXIT_LIQUIDITY_TRAP"
    assert result.sub_signals["bridge_rotation"].flag == "CROSS_CHAIN_CAPITAL_INFLOW"

