"""Whale Activity Agent — Tracks macro entity flows via Nansen TGM.

26-point budget (paired with OnChain agent under the 65-pt whale/on-chain pillar).
Legacy 25-point FINCON Tier 1 scoring model:
  - Whale Netflow (15 pts):       Direction and magnitude of movement.
  - Whale Buy Pressure (10 pts):  Inflow vs Outflow ratio (Absorption).

Zero-Point Context (Passed to DeepSeek R1 for correlation):
  - Gross Volume (Inflow + Outflow)
  - Raw Buy Volume (Inflows)
  - Raw Sell Volume (Outflows)

Solana Ecosystem Extensions:
  - SOL: LST Flow Velocity scoring via _eval_solana_lst.
  - JUP: Router Dominance scoring via _eval_jup_routing.
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
from atlas.models.signal import AgentTelemetry, SubSignalResult
from atlas.core.asset_universe import is_helius_enabled
from atlas.providers.helius.onchain_signals import (
    helius_flow_is_actionable,
    normalize_solana_base,
)
from atlas.providers.nansen.models import FlowEntity, NansenSnapshot
from atlas.scoring.confluence import OnChainSignals
from atlas.providers.nansen.schemas import (
    BridgeFlowPayload,
    LSTFlowPayload,
    RetailFlowPayload,
)

MAX_SCORE = 26


class WhaleAgent(BaseAgent):
    """Evaluates Nansen TGM Whale segment flows and packs absolute volume context."""

    @property
    def name(self) -> str:
        return "whale"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.WHALE

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    # ------------------------------------------------------------------
    # Core scoring entry point — must stay under 40 lines
    # ------------------------------------------------------------------

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Route scoring based on asset; SOL/JUP get ecosystem-specific logic."""
        start_ms = time.monotonic()
        ctx = context or {}
        nansen: NansenSnapshot | None = ctx.get("nansen_snapshot")

        if not nansen or not isinstance(nansen, NansenSnapshot):
            logger.warning("whale_agent_data_missing | msg=Nansen MCP snapshot unavailable")
            return self._build_empty_result()

        flows = nansen.whale_flows
        convs: list[str] = []
        risks: list[str] = []
        sub_signals: dict[str, SubSignalResult] = {}

        asset_label = str(ctx.get("asset") or data.get("asset", ""))
        helius_oc = ctx.get("helius_onchain")
        use_helius = (
            isinstance(helius_oc, OnChainSignals)
            and helius_oc.data_source == "helius"
            and is_helius_enabled(asset_label)
            and helius_flow_is_actionable(helius_oc)
        )

        if use_helius:
            nf_pts, direction, nf_flag = self._eval_netflow(
                helius_oc.exchange_netflow_4h, convs, risks,
            )
            inflow = max(helius_oc.whale_inflow_usd, Decimal("0"))
            outflow = Decimal("0")
            if helius_oc.exchange_netflow_4h < Decimal("0"):
                outflow = abs(helius_oc.exchange_netflow_4h)
            bp_pts, bp_ratio, bp_flag = self._eval_pressure(
                inflow, outflow, convs, risks,
            )
            sub_signals["helius_exchange_netflow"] = SubSignalResult.model_construct(
                value="${:,.0f}".format(helius_oc.exchange_netflow_4h),
                flag=nf_flag,
            )
        else:
            nf_pts, direction, nf_flag = self._eval_netflow(
                flows.net_flow_usd, convs, risks,
            )
            bp_pts, bp_ratio, bp_flag = self._eval_pressure(
                flows.inflow_usd, flows.outflow_usd, convs, risks,
            )
            self._attach_base_signals(sub_signals, flows, nf_flag, bp_ratio, bp_flag)

        total_score = nf_pts + bp_pts

        asset: str = (
            normalize_solana_base(asset_label)
            if asset_label
            else str(data.get("asset", ""))
        )
        total_score = self._apply_ecosystem_routing(
            asset, data, total_score, sub_signals, convs, risks,
        )

        total_score = self._apply_divergence_and_bridge(
            data, nansen, total_score, sub_signals, convs, risks,
        )

        elapsed = (time.monotonic() - start_ms) * 1000
        return self._assemble(total_score, direction, convs, risks, sub_signals, elapsed)

    # ------------------------------------------------------------------
    # Baseline deterministic helpers
    # ------------------------------------------------------------------

    def _eval_netflow(
        self, net: Decimal, conv: list[str], risks: list[str],
    ) -> tuple[int, SignalDirection, str]:
        """Evaluate netflow magnitude for points (15 max)."""
        if net > Decimal("10000000"):
            conv.append("Whales are aggressively accumulating.")
            return 15, SignalDirection.BULLISH, "MASSIVE_ACCUMULATION"
        if net > Decimal("1000000"):
            return 8, SignalDirection.BULLISH, "MODERATE_ACCUMULATION"
        if net < Decimal("-10000000"):
            risks.append("Whales are actively dumping (>$10M outflow).")
            return 0, SignalDirection.BEARISH, "MASSIVE_DISTRIBUTION"
        if net < Decimal("0"):
            return 0, SignalDirection.BEARISH, "DISTRIBUTING"
        return 0, SignalDirection.NEUTRAL, "NEUTRAL_FLOW"

    def _eval_pressure(
        self, inflow: Decimal, outflow: Decimal, conv: list[str], risks: list[str],
    ) -> tuple[int, Decimal, str]:
        """Evaluate buy pressure ratio for points (10 max)."""
        safe_outflow = max(outflow, Decimal("1.0"))
        ratio = inflow / safe_outflow

        if ratio > Decimal("3.0") and inflow > Decimal("500000"):
            conv.append("Heavy whale bid bias detected.")
            return 10, ratio, "HEAVY_BID_BIAS"
        if ratio > Decimal("1.5") and inflow > Decimal("500000"):
            return 5, ratio, "MODERATE_BID_BIAS"
        if ratio < Decimal("0.33") and outflow > Decimal("500000"):
            risks.append("Heavy whale ask bias (Absorption failure risk).")
            return 0, ratio, "HEAVY_ASK_BIAS"
        return 0, ratio, "BALANCED_PRESSURE"

    # ------------------------------------------------------------------
    # Solana ecosystem: LST Flow Velocity
    # ------------------------------------------------------------------

    def _eval_solana_lst(
        self, lst_flows: list[LSTFlowPayload],
    ) -> tuple[int, SubSignalResult]:
        """Sum netflow_24h across LST contracts and flag accumulation/capitulation."""
        total = sum(
            (flow.netflow_24h for flow in lst_flows),
            start=Decimal("0"),
        )

        if total <= Decimal("-50000"):
            return -20, SubSignalResult.model_construct(
                value="{:,.0f} SOL".format(total),
                flag="LST_CAPITULATION_WARNING",
            )
        if total >= Decimal("50000"):
            return 15, SubSignalResult.model_construct(
                value="+{:,.0f} SOL".format(total),
                flag="LST_ACCUMULATION",
            )
        return 0, SubSignalResult.model_construct(
            value="{:,.0f} SOL".format(total),
            flag="LST_NEUTRAL",
        )

    # ------------------------------------------------------------------
    # Jupiter ecosystem: Router Dominance
    # ------------------------------------------------------------------

    def _eval_jup_routing(
        self, pct: Decimal,
    ) -> tuple[int, SubSignalResult]:
        """Evaluate smart money routing preference through JUP aggregator."""
        if pct < Decimal("60.0"):
            return -15, SubSignalResult.model_construct(
                value="{:.1f}%".format(pct),
                flag="JUP_MARKETSHARE_DECAY",
            )
        if pct > Decimal("85.0"):
            return 10, SubSignalResult.model_construct(
                value="{:.1f}%".format(pct),
                flag="SMART_MONEY_ROUTING_PREFERENCE_HIGH",
            )
        return 0, SubSignalResult.model_construct(
            value="{:.1f}%".format(pct),
            flag="JUP_ROUTING_NEUTRAL",
        )

    # ------------------------------------------------------------------
    # Ecosystem routing dispatcher
    # ------------------------------------------------------------------

    def _apply_ecosystem_routing(
        self,
        asset: str,
        data: dict[str, Any],
        base_score: int,
        sub_signals: dict[str, SubSignalResult],
        convs: list[str],
        risks: list[str],
    ) -> int:
        """Dispatch SOL/JUP-specific scoring; no-op for standard EVM assets."""
        if asset == "SOL":
            raw_lst: list[LSTFlowPayload] = data.get("lst_flows", [])
            delta, sig = self._eval_solana_lst(raw_lst)
            sub_signals["solana_lst_velocity"] = sig
            if delta < 0:
                risks.append("LST capitulation detected across Solana liquid staking.")
            elif delta > 0:
                convs.append("LST accumulation detected across Solana liquid staking.")
            return base_score + delta

        if asset == "JUP":
            raw_pct = data.get("jup_dominance_pct")
            if raw_pct is not None:
                pct = raw_pct if isinstance(raw_pct, Decimal) else Decimal(str(raw_pct))
                delta, sig = self._eval_jup_routing(pct)
                sub_signals["jup_router_dominance"] = sig
                if delta < 0:
                    risks.append("JUP router market share declining.")
                elif delta > 0:
                    convs.append("Smart money strongly preferring JUP aggregator.")
                return base_score + delta

        return base_score

    # ------------------------------------------------------------------
    # Retail Divergence & Bridge Rotation
    # ------------------------------------------------------------------

    def _apply_divergence_and_bridge(
        self,
        data: dict[str, Any],
        nansen: NansenSnapshot,
        base_score: int,
        sub_signals: dict[str, SubSignalResult],
        convs: list[str],
        risks: list[str],
    ) -> int:
        """Apply retail divergence and bridge rotation adjustments."""
        retail: RetailFlowPayload | None = data.get("retail_flows")
        if retail is not None:
            sm_flow = nansen.smart_money_dex_activity.net_flow_usd
            delta, sig = self._eval_retail_divergence(sm_flow, retail)
            sub_signals["retail_divergence"] = sig
            base_score += delta
            if delta < 0:
                risks.append("Retail buying into smart money distribution (exit liquidity trap).")
            elif delta > 0:
                convs.append("Smart money accumulating while retail sells (stealth accumulation).")

        bridges: list[BridgeFlowPayload] = data.get("bridge_flows", [])
        if bridges:
            delta, sig = self._eval_bridge_rotation(bridges)
            sub_signals["bridge_rotation"] = sig
            base_score += delta
            if delta > 0:
                convs.append("Massive cross-chain capital inflow targeting asset chain.")

        return base_score

    @staticmethod
    def _eval_retail_divergence(
        smart_money_netflow: Decimal,
        retail: RetailFlowPayload,
    ) -> tuple[int, SubSignalResult]:
        """Detect dangerous smart-money vs retail flow divergence."""
        sm_neg = smart_money_netflow < Decimal("0")
        retail_pos = retail.netflow_usd > Decimal("0")

        if sm_neg and retail_pos:
            return -15, SubSignalResult.model_construct(
                value="SM: ${:,.0f} / Retail: ${:,.0f}".format(
                    smart_money_netflow, retail.netflow_usd,
                ),
                flag="EXIT_LIQUIDITY_TRAP",
            )

        sm_pos = smart_money_netflow > Decimal("0")
        retail_neg = retail.netflow_usd < Decimal("0")

        if sm_pos and retail_neg:
            return 10, SubSignalResult.model_construct(
                value="SM: +${:,.0f} / Retail: ${:,.0f}".format(
                    smart_money_netflow, retail.netflow_usd,
                ),
                flag="SMART_MONEY_ACCUMULATION",
            )

        return 0, SubSignalResult.model_construct(
            value="SM: ${:,.0f} / Retail: ${:,.0f}".format(
                smart_money_netflow, retail.netflow_usd,
            ),
            flag="RETAIL_DIVERGENCE_NEUTRAL",
        )

    @staticmethod
    def _eval_bridge_rotation(
        bridge_flows: list[BridgeFlowPayload],
    ) -> tuple[int, SubSignalResult]:
        """Evaluate cross-chain bridge inflows for capital rotation signals."""
        total_inflow = sum(
            (b.net_inflow_usd for b in bridge_flows),
            start=Decimal("0"),
        )

        if total_inflow > Decimal("10000000"):
            return 10, SubSignalResult.model_construct(
                value="${:,.0f}".format(total_inflow),
                flag="CROSS_CHAIN_CAPITAL_INFLOW",
            )

        return 0, SubSignalResult.model_construct(
            value="${:,.0f}".format(total_inflow),
            flag="BRIDGE_FLOW_NEUTRAL",
        )

    # ------------------------------------------------------------------
    # Signal attachment helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _attach_base_signals(
        sub_signals: dict[str, SubSignalResult],
        flows: FlowEntity,
        nf_flag: str,
        bp_ratio: Decimal,
        bp_flag: str,
    ) -> None:
        """Attach baseline whale flow sub-signals."""
        sub_signals["whale_netflow"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(flows.net_flow_usd), flag=nf_flag,
        )
        sub_signals["whale_buy_pressure"] = SubSignalResult.model_construct(
            value="{:.2f}x".format(bp_ratio), flag=bp_flag,
        )
        gross_vol = flows.inflow_usd + flows.outflow_usd
        sub_signals["gross_whale_volume"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(gross_vol), flag="TOTAL_PARTICIPATION",
        )
        sub_signals["raw_whale_inflows"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(flows.inflow_usd), flag="RAW_BUY_SIDE",
        )
        sub_signals["raw_whale_outflows"] = SubSignalResult.model_construct(
            value="${:,.0f}".format(flows.outflow_usd), flag="RAW_SELL_SIDE",
        )

    # ------------------------------------------------------------------
    # Result assembly
    # ------------------------------------------------------------------

    def _assemble(
        self,
        pts: int,
        direction: SignalDirection,
        conv: list[str],
        risks: list[str],
        sub_signals: dict[str, SubSignalResult],
        elapsed: float,
    ) -> AgentResult:
        """Assemble final frozen AgentResult."""
        capped = max(0, min(MAX_SCORE, pts))
        return AgentResult.model_construct(
            agent_name=self.name,
            score=capped,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation="Whale flow score {}/{}.".format(capped, MAX_SCORE),
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
            explanation="Failed to parse Whale MCP data",
            convergences=[],
            risks=["WHALE_DATA_MISSING"],
            veto=False,
            sub_signals={},
            telemetry=AgentTelemetry(latency_ms=0.0),
        )
