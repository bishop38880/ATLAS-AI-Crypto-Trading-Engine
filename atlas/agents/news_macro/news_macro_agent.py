"""News and Macro Agent — System-wide Conviction Suppressor.

ATLAS Intelligence Layer.

Max Score: 0
Function: Outputs negative ``conviction_suppression`` values based on
imminent economic calendar events, token unlocks (via Token Unlocks MCP),
or breaking regulatory news.

**Exit Liquidity Guardrail (Session 26):**
Calls the Token Unlocks MCP server's ``evaluate_exit_liquidity_risk``
tool to determine whether an impending cliff unlock threatens to
overwhelm circulating supply.  If ``conviction_suppression = True`` is
returned, a -15 penalty is applied to veto long positions.
"""

import asyncio
import time
from decimal import Decimal
from typing import Any

from loguru import logger

from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentTier,
    BaseAgent,
    SignalDirection,
)
from atlas.models.signal import SubSignalResult
from atlas.agents.base import AgentTelemetry
from atlas.core.mcp_client import MCPClient
from atlas.shared.serialisation import decode_json


# ─── Dune Query ID Registry ─────────────────────────────────────────────────
# Maps asset symbols to the community Dune query IDs that track
# on-chain vesting contract unlock schedules.
# These query IDs should be updated as new, more accurate queries
# are authored by the Dune community.
DUNE_QUERY_MAP: dict[str, int] = {
    "ARB": 3190234,
    "OP": 3190235,
    "APT": 3190236,
    "SUI": 3190237,
    "SEI": 3190238,
    "TIA": 3190239,
    "INJ": 3190240,
    "FIL": 3190241,
    "RENDER": 3190242,
    "FET": 3190243,
    "ONDO": 3190244,
    "TAO": 3190245,
    "LDO": 3190246,
    "JUP": 3190247,
}

_UNLOCK_PENALTY = -15
_COINGECKO_SUPPLY_SHOCK_PENALTY = -10
_FOMC_PENALTY = -20
_DEFAULT_THRESHOLD = "2.0"


class NewsMacroAgent(BaseAgent):
    """Conviction suppressor for external shock events.

    ATLAS Intelligence Layer.

    Integrates with the Token Unlocks MCP for real-time exit liquidity
    evaluation via DefiLlama (circulating supply) and Dune Analytics
    (on-chain vesting schedules).
    """

    def __init__(
        self,
        unlock_mcp_client: MCPClient | None = None,
    ) -> None:
        """Initialize with optional MCP client.

        Args:
            unlock_mcp_client: MCPClient connected to the
                ``polaris-token-unlocks`` MCP server.  When ``None``,
                falls back to context-dict based evaluation.
        """
        self._unlock_mcp = unlock_mcp_client

    @property
    def name(self) -> str:
        return "news_macro"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.NEWS_MACRO

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> AgentResult:
        """Evaluate macro risks and token unlock threats."""
        start_ms = time.monotonic()
        ctx = context or {}
        risks: list[str] = []
        sub_signals: dict[str, SubSignalResult] = {}

        suppression = self._evaluate_fomc(ctx, risks, sub_signals)
        suppression += self._coingecko_supply_shock_penalty(ctx, risks, sub_signals)
        suppression += await self._evaluate_unlocks(
            ctx, risks, sub_signals,
        )
        sub_signals["conviction_suppression"] = SubSignalResult.model_construct(
            value=str(suppression), flag="APPLIED_PENALTY", metadata={"amount": suppression}
        )
        elapsed = (time.monotonic() - start_ms) * 1000
        return self._assemble(suppression, risks, sub_signals, elapsed)

    @staticmethod
    def _evaluate_fomc(
        ctx: dict[str, Any], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Check economic calendar for FOMC overhang."""
        if ctx.get("fomc_within_24h", False):
            risks.append("FOMC rate decision within 24 hours. High volatility risk.")
            sigs["economic_calendar"] = SubSignalResult.model_construct(
                value="FOMC_MEETING", flag="SEVERE_MACRO_OVERHANG",
            )
            return _FOMC_PENALTY
        sigs["economic_calendar"] = SubSignalResult.model_construct(
            value="CLEAR", flag="NO_IMMINENT_EVENTS",
        )
        return 0

    @staticmethod
    def _coingecko_supply_shock_penalty(
        ctx: dict[str, Any], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Apply penalty when CoinGecko circulating supply jumped vs prior Redis sample."""
        shock = ctx.get("coingecko_supply_shock")
        if not isinstance(shock, dict):
            return 0
        if not shock.get("likely_unlock_or_dilution"):
            return 0
        pct = shock.get("circulating_pct_change_vs_prior")
        if isinstance(pct, (int, float)):
            risks.append(
                "CoinGecko: circulating supply step (+{:.2f}%) suggests unlock/dilution.".format(
                    float(pct),
                ),
            )
            sig_val = "{:.2f}%".format(float(pct))
        else:
            risks.append(
                "CoinGecko: circulating supply step suggests unlock/dilution.",
            )
            sig_val = "spike"
        sigs["coingecko_supply"] = SubSignalResult.model_construct(
            value=sig_val,
            flag="CIRC_SUPPLY_JUMP",
        )
        return _COINGECKO_SUPPLY_SHOCK_PENALTY

    async def _evaluate_unlocks(
        self, ctx: dict[str, Any], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Evaluate exit liquidity risk via Token Unlocks MCP.

        If the MCP client is available and the asset has a registered
        Dune query ID, calls ``evaluate_exit_liquidity_risk``.
        Otherwise falls back to the legacy context-dict pattern.
        """
        asset = ctx.get("asset", "")
        symbol = asset.split("/")[0] if "/" in asset else asset
        symbol = symbol.upper()

        if self._unlock_mcp and symbol in DUNE_QUERY_MAP:
            return await self._evaluate_via_mcp(
                symbol, risks, sigs,
            )
        return self._evaluate_via_context(ctx, risks, sigs)

    async def _evaluate_via_mcp(
        self, symbol: str, risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Call Token Unlocks MCP for real-time evaluation."""
        query_id = DUNE_QUERY_MAP[symbol]
        try:
            raw = await self._unlock_mcp.call_tool(  # type: ignore[union-attr]
                "evaluate_exit_liquidity_risk",
                {
                    "symbol": symbol,
                    "dune_query_id": query_id,
                    "threshold_percent": _DEFAULT_THRESHOLD,
                },
                timeout=15.0,
            )
            return self._parse_mcp_response(raw, symbol, risks, sigs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "Token Unlocks MCP call failed | symbol={} | err={}",
                symbol, exc,
            )
            sigs["token_unlocks"] = SubSignalResult.model_construct(
                value="MCP_DEGRADED", flag="FALLBACK",
            )
            return 0

    def _parse_mcp_response(
        self, raw: Any, symbol: str,
        risks: list[str], sigs: dict[str, SubSignalResult],
    ) -> int:
        """Parse MCP JSON-RPC response into penalty."""
        if raw is None:
            sigs["token_unlocks"] = SubSignalResult.model_construct(
                value="NO_DATA", flag="MCP_EMPTY",
            )
            return 0

        report = self._decode_report(raw)
        suppressed = report.get("conviction_suppression", False)
        impact = report.get("impact_pct", "0")
        risk_tier = report.get("risk_tier", "NORMAL")

        sigs["token_unlocks"] = SubSignalResult.model_construct(
            value=f"{impact}%",
            flag=f"UNLOCK_{risk_tier}",
            metadata={
                "impact_pct": str(impact),
                "risk_tier": risk_tier,
                "suppressed": suppressed,
                "symbol": symbol,
            },
        )
        if suppressed:
            risks.append(
                f"EXIT LIQUIDITY: {symbol} cliff unlock "
                f"({impact}% of circ supply) within 72h.",
            )
            return _UNLOCK_PENALTY
        return 0

    @staticmethod
    def _decode_report(raw: Any) -> dict[str, Any]:
        """Decode MCP response to dict.

        MCPClient.call_tool returns the JSON-RPC ``result`` field,
        which for MCP servers is ``list[TextContent]`` — i.e. a list
        of dicts like ``[{"type": "text", "text": "...json..."}]``.
        We extract the ``text`` payload from the first element and
        decode it to a dict.
        """
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, list) and len(raw) > 0:
            first = raw[0]
            text = first.get("text", "") if isinstance(first, dict) else ""
            if text:
                return NewsMacroAgent._decode_report(text)
            return {}
        if isinstance(raw, (str, bytes)):
            decoded = decode_json(
                raw.encode("utf-8") if isinstance(raw, str) else raw, type=dict,
            )
            return decoded if isinstance(decoded, dict) else {}
        return {}

    @staticmethod
    def _evaluate_via_context(
        ctx: dict[str, Any], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Legacy fallback: read cliff_unlock_pct_7d from context."""
        pct = Decimal(str(ctx.get("cliff_unlock_pct_7d", "0")))
        if pct > Decimal("5"):
            risks.append(
                f"Major CLIFF token unlock ({pct}%) within 7 days.",
            )
            sigs["token_unlocks"] = SubSignalResult.model_construct(
                value=f"{pct}%", flag="DUMP_RISK",
            )
            return _UNLOCK_PENALTY
        sigs["token_unlocks"] = SubSignalResult.model_construct(
            value="<5%", flag="NORMAL_SCHEDULE",
        )
        return 0

    def _assemble(
        self, suppression: int, risks: list[str],
        sub_signals: dict[str, SubSignalResult], elapsed: float,
    ) -> AgentResult:
        """Build the final suppression result."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=0,
            weight=1.0,
            direction=SignalDirection.NEUTRAL,
            explanation=f"Macro suppression penalty: {suppression}.",
            convergences=[],
            risks=risks,
            veto=False,
            sub_signals=sub_signals,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )
