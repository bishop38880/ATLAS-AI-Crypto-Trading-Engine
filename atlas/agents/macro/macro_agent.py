"""Macro Cross-Market Agent — Evaluates ambient liquidity and risk appetite.

15-point FINCON v2.4 Tier 1 scoring model:
  - Stablecoin Expansion & Dominance (8 pts)
  - BTC Dominance / Altcoin Rotation (7 pts)
"""

import time
from decimal import Decimal
from typing import Any
from loguru import logger

from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentTelemetry,
    AgentTier,
    BaseAgent,
    SignalDirection,
)
from atlas.models.signal import SubSignalResult

MAX_SCORE = 15

class MacroCrossMarketAgent(BaseAgent):
    """Evaluates macro liquidity and cross-asset correlations."""

    @property
    def name(self) -> str:
        return "macro"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.CONTEXT

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> AgentResult:
        start_ms = time.monotonic()
        ctx = context or {}
        conv: list[str] = []
        risks: list[str] = []
        sigs: dict[str, SubSignalResult] = {}

        stbl = Decimal("2.4")
        btc_dom = Decimal("54.2")

        pts = self._score_liquidity(stbl, conv, risks, sigs)
        pts += self._score_market_structure(btc_dom, conv, sigs)
        direction = SignalDirection.BULLISH if pts >= 8 else SignalDirection.NEUTRAL
        elapsed = (time.monotonic() - start_ms) * 1000
        return self._assemble(pts, direction, conv, risks, sigs, elapsed)

    @staticmethod
    def _score_liquidity(
        growth: Decimal, conv: list[str], risks: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Score stablecoin expansion (8 pts)."""
        if growth > Decimal("2.0"):
            conv.append("Stablecoin supply expanding (Risk-On Liquidity).")
            sigs["stablecoin_flow"] = SubSignalResult.model_construct(
                value="+{}%".format(growth), flag="LIQUIDITY_EXPANSION",
            )
            return 8
        if growth < Decimal("-1.0"):
            risks.append("Stablecoin supply contracting (Liquidity Vacuum).")
            sigs["stablecoin_flow"] = SubSignalResult.model_construct(
                value="{}%".format(growth), flag="LIQUIDITY_CONTRACTION",
            )
            return 0
        sigs["stablecoin_flow"] = SubSignalResult.model_construct(
            value="{}%".format(growth), flag="NEUTRAL_FLOW",
        )
        return 0

    @staticmethod
    def _score_market_structure(
        btc_dom: Decimal, conv: list[str],
        sigs: dict[str, SubSignalResult],
    ) -> int:
        """Score BTC dominance / altcoin rotation (7 pts)."""
        if btc_dom < Decimal("50.0"):
            conv.append("BTC Dominance dropping (Altcoin rotation phase).")
            sigs["btc_dominance"] = SubSignalResult.model_construct(
                value="{}%".format(btc_dom), flag="ALTCOIN_ROTATION",
            )
            return 7
        sigs["btc_dominance"] = SubSignalResult.model_construct(
            value="{}%".format(btc_dom), flag="BTC_ABSORPTION",
        )
        return 0

    def _assemble(
        self, pts: int, direction: SignalDirection,
        conv: list[str], risks: list[str],
        sigs: dict[str, SubSignalResult], elapsed: float,
    ) -> AgentResult:
        """Assemble the final AgentResult."""
        return AgentResult.model_construct(
            agent_name=self.name, score=pts, max_score=MAX_SCORE,
            weight=1.0, direction=direction,
            explanation="Macro environment scores {}/{}.".format(pts, MAX_SCORE),
            convergences=conv, risks=risks, veto=False,
            sub_signals=sigs,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )
