"""Tests for NewsMacroAgent — Token Unlocks MCP integration.

Validates:
1. Legacy context-dict fallback path (no MCP client).
2. MCP-driven evaluation with suppression trigger.
3. MCP-driven evaluation with no suppression.
4. Graceful degradation when MCP call fails.
5. FOMC overhang penalty.
6. Combined FOMC + unlock penalty stacking.
7. Asset symbol extraction from trading pair format.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.agents.news_macro.news_macro_agent import (
    DUNE_QUERY_MAP,
    NewsMacroAgent,
)


# ═════════════════════════════ Fixtures ══════════════════════════════════════


def _make_mcp_response(
    suppressed: bool,
    impact_pct: str = "5.0",
    risk_tier: str = "CRITICAL",
) -> dict[str, Any]:
    """Build a mock MCP JSON-RPC response dict."""
    return {
        "conviction_suppression": suppressed,
        "impact_pct": impact_pct,
        "risk_tier": risk_tier,
        "symbol": "ARB",
        "threshold_pct": "2.0",
        "circulating_supply": "1000000000",
        "total_unlock_amount_72h": "50000000",
        "unlock_events_72h": 1,
    }


def _mock_mcp_client(response: Any = None, *, raises: Exception | None = None) -> MagicMock:
    """Create a mock MCPClient with configurable call_tool behavior."""
    client = MagicMock()
    if raises:
        client.call_tool = AsyncMock(side_effect=raises)
    else:
        client.call_tool = AsyncMock(return_value=response)
    return client


# ═══════════════════════ Legacy Context Fallback ════════════════════════════


class TestLegacyFallback:
    """Tests for the context-dict based evaluation (no MCP)."""

    @pytest.mark.asyncio
    async def test_legacy_no_unlock_risk(self) -> None:
        """Sub-5% unlock → no suppression, NORMAL_SCHEDULE flag."""
        agent = NewsMacroAgent(unlock_mcp_client=None)
        result = await agent.score(
            data={},
            context={"cliff_unlock_pct_7d": "3.0"},
        )
        assert result.sub_signals["token_unlocks"].flag == "NORMAL_SCHEDULE"
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == 0

    @pytest.mark.asyncio
    async def test_legacy_high_unlock_triggers_penalty(self) -> None:
        """Above 5% unlock in context → -15 penalty."""
        agent = NewsMacroAgent(unlock_mcp_client=None)
        result = await agent.score(
            data={},
            context={"cliff_unlock_pct_7d": "7.5"},
        )
        assert result.sub_signals["token_unlocks"].flag == "DUMP_RISK"
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == -15

    @pytest.mark.asyncio
    async def test_legacy_uses_decimal_not_float(self) -> None:
        """Confirm Decimal comparison, not float."""
        agent = NewsMacroAgent(unlock_mcp_client=None)
        # Exactly 5.0 should NOT trigger (we check > 5)
        result = await agent.score(
            data={},
            context={"cliff_unlock_pct_7d": "5.0"},
        )
        assert result.sub_signals["token_unlocks"].flag == "NORMAL_SCHEDULE"


# ═════════════════════ MCP-Driven Evaluation ════════════════════════════════


class TestMCPIntegration:
    """Tests for the MCP-driven unlock evaluation."""

    @pytest.mark.asyncio
    async def test_mcp_suppression_triggered(self) -> None:
        """MCP returns conviction_suppression=True → -15 penalty."""
        response = _make_mcp_response(suppressed=True, impact_pct="5.0")
        mcp = _mock_mcp_client(response)
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        mcp.call_tool.assert_called_once()
        unlock_sig = result.sub_signals["token_unlocks"]
        assert unlock_sig.flag == "UNLOCK_CRITICAL"
        assert unlock_sig.metadata["suppressed"] is True
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == -15
        assert len(result.risks) == 1
        assert "EXIT LIQUIDITY" in result.risks[0]

    @pytest.mark.asyncio
    async def test_mcp_no_suppression(self) -> None:
        """MCP returns conviction_suppression=False → no penalty."""
        response = _make_mcp_response(
            suppressed=False, impact_pct="0.5", risk_tier="NORMAL",
        )
        mcp = _mock_mcp_client(response)
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        unlock_sig = result.sub_signals["token_unlocks"]
        assert unlock_sig.flag == "UNLOCK_NORMAL"
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == 0

    @pytest.mark.asyncio
    async def test_mcp_failure_degrades_gracefully(self) -> None:
        """MCP call failure → 0 penalty, FALLBACK flag."""
        mcp = _mock_mcp_client(raises=TimeoutError("MCP timed out"))
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        assert result.sub_signals["token_unlocks"].flag == "FALLBACK"
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == 0

    @pytest.mark.asyncio
    async def test_mcp_null_response(self) -> None:
        """MCP returns None → 0 penalty, MCP_EMPTY flag."""
        mcp = _mock_mcp_client(response=None)
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        assert result.sub_signals["token_unlocks"].flag == "MCP_EMPTY"

    @pytest.mark.asyncio
    async def test_no_mcp_for_unmapped_asset(self) -> None:
        """Asset not in DUNE_QUERY_MAP → falls back to context."""
        mcp = _mock_mcp_client()
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "BTC/USDT", "cliff_unlock_pct_7d": "0"},
        )

        # MCP should NOT have been called for BTC (not in query map)
        mcp.call_tool.assert_not_called()
        assert result.sub_signals["token_unlocks"].flag == "NORMAL_SCHEDULE"

    @pytest.mark.asyncio
    async def test_mcp_textcontent_list_format(self) -> None:
        """MCPClient returns list[TextContent] — must unwrap correctly.

        This is the REAL wire format: MCPClient.call_tool returns
        ``[{"type": "text", "text": "{...json...}"}]``.
        _decode_report must extract the text and decode the inner JSON.
        """
        import msgspec

        inner_report = _make_mcp_response(suppressed=True, impact_pct="3.5")
        wire_format = [{"type": "text", "text": msgspec.json.encode(inner_report).decode("utf-8")}]
        mcp = _mock_mcp_client(wire_format)
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        unlock_sig = result.sub_signals["token_unlocks"]
        assert unlock_sig.flag == "UNLOCK_CRITICAL"
        assert unlock_sig.metadata["suppressed"] is True
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == -15

    @pytest.mark.asyncio
    async def test_mcp_empty_textcontent_list(self) -> None:
        """Empty list from MCP → treated as no data, 0 penalty."""
        mcp = _mock_mcp_client([])
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT"},
        )

        # Empty list → _decode_report returns {} → suppressed=False
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == 0


# ═══════════════════════ FOMC Overhang ══════════════════════════════════════


class TestFOMCOverhang:
    """Tests for the FOMC economic calendar suppression."""

    @pytest.mark.asyncio
    async def test_fomc_penalty(self) -> None:
        """FOMC within 24h → -20 penalty."""
        agent = NewsMacroAgent()
        result = await agent.score(
            data={},
            context={"fomc_within_24h": True},
        )
        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == -20
        assert result.sub_signals["economic_calendar"].flag == "SEVERE_MACRO_OVERHANG"

    @pytest.mark.asyncio
    async def test_no_fomc(self) -> None:
        """No FOMC → no penalty from calendar."""
        agent = NewsMacroAgent()
        result = await agent.score(data={}, context={})
        assert result.sub_signals["economic_calendar"].flag == "NO_IMMINENT_EVENTS"


# ═══════════════════════ CoinGecko supply shock ════════════════════════════


class TestCoinGeckoSupplyShock:
    """Circulating-supply jump from orchestrator context."""

    @pytest.mark.asyncio
    async def test_coingecko_shock_penalty(self) -> None:
        """Likely dilution flag from CoinGecko tracker applies -10."""
        agent = NewsMacroAgent(unlock_mcp_client=None)
        result = await agent.score(
            data={},
            context={
                "cliff_unlock_pct_7d": "3.0",
                "coingecko_supply_shock": {
                    "likely_unlock_or_dilution": True,
                    "circulating_pct_change_vs_prior": 1.2,
                },
            },
        )
        assert int(result.sub_signals["conviction_suppression"].metadata["amount"]) == -10
        assert result.sub_signals["coingecko_supply"].flag == "CIRC_SUPPLY_JUMP"

    @pytest.mark.asyncio
    async def test_coingecko_shock_stacks_with_legacy_unlock(self) -> None:
        agent = NewsMacroAgent(unlock_mcp_client=None)
        result = await agent.score(
            data={},
            context={
                "cliff_unlock_pct_7d": "7.0",
                "coingecko_supply_shock": {
                    "likely_unlock_or_dilution": True,
                    "circulating_pct_change_vs_prior": 0.8,
                },
            },
        )
        assert int(result.sub_signals["conviction_suppression"].metadata["amount"]) == -25


# ═══════════════════════ Combined Penalties ══════════════════════════════════


class TestCombinedPenalties:
    """Test stacking of multiple suppression sources."""

    @pytest.mark.asyncio
    async def test_fomc_plus_unlock_stacks(self) -> None:
        """FOMC (-20) + unlock (-15) = -35 total suppression."""
        response = _make_mcp_response(suppressed=True)
        mcp = _mock_mcp_client(response)
        agent = NewsMacroAgent(unlock_mcp_client=mcp)

        result = await agent.score(
            data={},
            context={"asset": "ARB/USDT", "fomc_within_24h": True},
        )

        suppression = result.sub_signals["conviction_suppression"]
        assert int(suppression.metadata["amount"]) == -35

    @pytest.mark.asyncio
    async def test_agent_result_direction_neutral(self) -> None:
        """NewsMacroAgent always outputs NEUTRAL direction."""
        agent = NewsMacroAgent()
        result = await agent.score(data={}, context={})
        from atlas.agents.base import SignalDirection
        assert result.direction == SignalDirection.NEUTRAL

    @pytest.mark.asyncio
    async def test_agent_score_always_zero(self) -> None:
        """Max score is 0 — this agent only suppresses."""
        agent = NewsMacroAgent()
        result = await agent.score(data={}, context={})
        assert result.score == 0
        assert result.max_score == 0
