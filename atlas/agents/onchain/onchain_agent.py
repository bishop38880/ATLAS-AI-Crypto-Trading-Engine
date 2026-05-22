"""OnChain Analyst Agent — Nansen Smart Money & Wallet Intelligence.

Hardened for Sentinel v3.0:
- Uses Nansen MCP snapshot exclusively.
- Implements the 220-point factor (Nansen portion: 7 pts).
- Strict Decimal precision and loguru compliance.
"""

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
from atlas.agents.base import AgentTelemetry
from atlas.models.signal import SubSignalResult
from atlas.providers.coingecko.adapter import DexPoolData
from atlas.core.asset_universe import is_helius_enabled
from atlas.providers.helius.onchain_signals import helius_flow_is_actionable
from atlas.providers.nansen.models import NansenSnapshot
from atlas.scoring.confluence import OnChainSignals

# This agent's share of the 220-point stack (on-chain slice of the 65-pt whale pillar).
MAX_SCORE = 39


class OnChainAgent(BaseAgent):
    """Evaluates On-Chain structures and smart money flows."""

    @property
    def name(self) -> str:
        return "onchain"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.ONCHAIN

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> AgentResult:
        """Calculate agent score based on Nansen intelligence."""
        start_ms = time.monotonic()
        ctx = context or {}
        nansen = ctx.get("nansen_snapshot")
        helius_oc = ctx.get("helius_onchain")
        asset_label = str(ctx.get("asset") or data.get("asset", ""))
        helius_active = (
            isinstance(helius_oc, OnChainSignals)
            and helius_oc.data_source == "helius"
            and is_helius_enabled(asset_label)
            and helius_flow_is_actionable(helius_oc)
        )

        if (not nansen or not isinstance(nansen, NansenSnapshot)) and not helius_active:
            logger.warning("onchain_agent_missing_data | agent=onchain | status=STUB")
            return self._build_empty_result()

        conv: list[str] = []
        risks: list[str] = []
        sub_signals: dict[str, SubSignalResult] = {}

        sm_pts = 0
        if nansen and isinstance(nansen, NansenSnapshot):
            sm_pts = self._score_smart_money(nansen, conv, risks, sub_signals)

        if helius_active:
            ex_pts = self._score_helius_exchange_flows(helius_oc, conv, risks, sub_signals)
        elif nansen and isinstance(nansen, NansenSnapshot):
            ex_pts = self._score_exchange_flows(nansen, conv, risks, sub_signals)
        else:
            ex_pts = 0

        pools_raw = ctx.get("gecko_dex_pools")
        if pools_raw:
            self._attach_gecko_pool_context(pools_raw, sub_signals)

        total_pts = sm_pts + ex_pts
        scaled = int(
            min(MAX_SCORE, round(total_pts / 7.0 * float(MAX_SCORE))),
        )
        if nansen and isinstance(nansen, NansenSnapshot):
            direction = self._resolve_direction(nansen)
        elif helius_active:
            direction = self._resolve_direction_from_helius(helius_oc)
        else:
            direction = SignalDirection.NEUTRAL

        elapsed = (time.monotonic() - start_ms) * 1000
        return self._assemble(scaled, direction, conv, risks, sub_signals, elapsed)

    def _score_smart_money(
        self, nansen: NansenSnapshot,
        conv: list[str], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Evaluate smart money accumulation/distribution (Max 4 pts)."""
        pts = 0
        netflow = nansen.smart_money_flow_24h.net_flow_usd
        
        if netflow > 0:
            pts = int(Decimal("4") * Decimal(str(nansen.confidence_modifier)))
            conf_pct = int(nansen.confidence_modifier * 100)
            conv.append(f"Smart Money accumulation detected ({conf_pct}% confidence)")
            flag = "ACCUMULATING"
        elif netflow < 0:
            risks.append("Smart Money distribution detected.")
            flag = "DISTRIBUTING"
        else:
            flag = "NEUTRAL"
            
        sigs["smart_money_signal"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(netflow), flag=flag,
        )
        return pts

    def _score_helius_exchange_flows(
        self,
        helius: OnChainSignals,
        conv: list[str],
        risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Score exchange netflow from Helius Solana flow tracker (SOL/JUP)."""
        netflow = helius.exchange_netflow_4h
        if netflow < Decimal("-1000000"):
            conv.append(
                "Helius: net exchange outflow ${:,.0f} (4h)".format(abs(netflow)),
            )
            flag = "EXCHANGE_OUTFLOW"
            pts = 3
        elif netflow > Decimal("1000000"):
            risks.append(
                "Helius: exchange inflow ${:,.0f} (4h)".format(netflow),
            )
            flag = "EXCHANGE_INFLOW"
            pts = 0
        else:
            flag = "NEUTRAL"
            pts = 1 if netflow < Decimal("0") else 0

        sigs["helius_exchange_netflow"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(netflow),
            flag=flag,
        )
        return pts

    @staticmethod
    def _resolve_direction_from_helius(helius: OnChainSignals) -> SignalDirection:
        if helius.exchange_netflow_4h < Decimal("-500000"):
            return SignalDirection.BULLISH
        if helius.exchange_netflow_4h > Decimal("500000"):
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _score_exchange_flows(
        self, nansen: NansenSnapshot,
        conv: list[str], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Evaluate exchange netflows (Max 3 pts)."""
        pts = 0
        ex_signal = nansen.exchange_netflow.signal
        netflow = nansen.exchange_netflow.netflow_usd
        
        if ex_signal == "BULLISH":
            pts = 3
            conv.append("Net exchange outflow detected: ${:,.0f}".format(abs(netflow)))
            flag = "EXCHANGE_OUTFLOW"
        elif ex_signal == "BEARISH":
            risks.append("Exchange inflow detected: ${:,.0f} (Potential Dump)".format(netflow))
            flag = "EXCHANGE_INFLOW"
        else:
            flag = "NEUTRAL"
            
        sigs["exchange_netflow"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(netflow), flag=flag,
        )
        return pts

    def _resolve_direction(self, nansen: NansenSnapshot) -> SignalDirection:
        """Determine overall directional bias."""
        if nansen.smart_money_signal == "ACCUMULATING":
            return SignalDirection.BULLISH
        if nansen.smart_money_signal == "DISTRIBUTING":
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _attach_gecko_pool_context(
        self,
        pools_raw: Any,
        sub_signals: dict[str, SubSignalResult],
    ) -> None:
        """Surface GeckoTerminal DeFi pools (orchestrator passes ``gecko_dex_pools``)."""
        pools = [p for p in pools_raw if isinstance(p, DexPoolData)]
        if not pools:
            return
        top = max(pools, key=lambda p: float(p.liquidity_usd))
        sub_signals["gecko_terminal_pools"] = SubSignalResult.model_construct(
            value="{} @ {}".format(top.dex_name, top.network),
            flag="DEFI_LIQUIDITY_TOP_POOL",
        )

    def _assemble(
        self, pts: int, direction: SignalDirection,
        conv: list[str], risks: list[str],
        sub_signals: dict[str, SubSignalResult], elapsed: float,
    ) -> AgentResult:
        """Assemble final AgentResult."""
        explanation = "Nansen Intelligence: {}/{} points.".format(pts, MAX_SCORE)
        if any("confidence" in c.lower() and "50" in c for c in conv):
            explanation += " [LOW_CONFIDENCE]"

        return AgentResult.model_construct(
            agent_name=self.name,
            score=pts,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation=explanation,
            convergences=conv,
            risks=risks,
            veto=False,
            sub_signals=sub_signals,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )

    def _build_empty_result(self) -> AgentResult:
        """Return zero-score result on data failure."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=SignalDirection.NEUTRAL,
            explanation="Nansen data unavailable",
            convergences=[],
            risks=["DATA_UNAVAILABLE"],
            veto=False,
            sub_signals={},
            telemetry=AgentTelemetry(latency_ms=0.0),
        )
