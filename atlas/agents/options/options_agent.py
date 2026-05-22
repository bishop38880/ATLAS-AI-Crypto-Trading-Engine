"""Options Intelligence agent — Deribit options sub-component of derivatives."""

from __future__ import annotations

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
from atlas.agents.options.scoring import (
    calculate_iv_skew_points,
    calculate_put_call_ratio_points,
    calculate_spot_vs_max_pain_points,
    calculate_term_structure_points,
    resolve_eval_side,
)
from atlas.providers.deribit.models import OptionsIntelligence

MAX_SCORE = 18
_COVERED_ASSETS = frozenset({"BTCUSDT", "ETHUSDT"})


class OptionsIntelligenceAgent(BaseAgent):
    """Scores BTC/ETH via Deribit options; neutral for other assets."""

    @property
    def name(self) -> str:
        return "options_intelligence_agent"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.DERIVATIVES

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    @property
    def max_points(self) -> int:
        return MAX_SCORE

    async def score(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        start_ms = time.monotonic()
        ctx = context or {}
        asset = str(
            data.get("asset") or ctx.get("asset") or "BTCUSDT",
        ).upper().replace("/", "")

        if asset not in _COVERED_ASSETS:
            return self._make_zero_result(
                "Options intelligence: asset not covered (BTC/ETH only)",
            )

        intelligence = self._extract_intelligence(data, ctx)
        if intelligence is None:
            return self._make_zero_result("Options intelligence: no Deribit data")

        return self._score_intelligence(intelligence, ctx, start_ms)

    def _extract_intelligence(
        self,
        data: dict[str, Any],
        ctx: dict[str, Any],
    ) -> OptionsIntelligence | None:
        raw = data.get("options_intelligence") or ctx.get("options_intelligence")
        if raw is None:
            return None
        if isinstance(raw, OptionsIntelligence):
            return raw
        if isinstance(raw, dict):
            return OptionsIntelligence(**raw)
        return None

    def _score_intelligence(
        self,
        intelligence: OptionsIntelligence,
        ctx: dict[str, Any],
        start_ms: float,
    ) -> AgentResult:
        eval_side = resolve_eval_side(ctx)
        pain_pts = calculate_spot_vs_max_pain_points(intelligence, eval_side)
        pcr_pts = calculate_put_call_ratio_points(intelligence, eval_side)
        skew_pts = calculate_iv_skew_points(intelligence, eval_side)
        term_pts = calculate_term_structure_points(intelligence)
        total = min(pain_pts + pcr_pts + skew_pts + term_pts, MAX_SCORE)
        elapsed = (time.monotonic() - start_ms) * 1000
        direction = self._determine_direction(intelligence, total)

        explanation = (
            "OPTIONS: max_pain={} spot_pct={}% pcr_vol={} skew={} term={} → {}pts"
        ).format(
            intelligence.max_pain_price,
            intelligence.spot_to_max_pain_pct,
            intelligence.put_call_ratio_volume,
            intelligence.iv_skew,
            "contango" if intelligence.contango else "backwardation",
            total,
        )
        logger.debug(
            "options_intel_scored | asset={} | pts={} | eval_side={}",
            intelligence.asset,
            total,
            eval_side,
        )
        return AgentResult.model_construct(
            agent_name=self.name,
            score=total,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation=explanation,
            convergences=[],
            risks=[],
            veto=False,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )

    def _determine_direction(
        self,
        intelligence: OptionsIntelligence,
        score: int,
    ) -> SignalDirection:
        if score <= 0:
            return SignalDirection.NEUTRAL
        pct = intelligence.spot_to_max_pain_pct
        if pct < Decimal("-2"):
            return SignalDirection.BULLISH
        if pct > Decimal("2"):
            return SignalDirection.BEARISH
        pcr = intelligence.put_call_ratio_volume
        if pcr > Decimal("1.2"):
            return SignalDirection.BULLISH
        if pcr < Decimal("0.8"):
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL
