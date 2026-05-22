"""Sentiment analyst — Alternative.me F&G + NLP + funding proxy (35/220 pillar).

Three-source blend (Strategy Doc v1.1 §3.3):
  - Alternative.me Fear & Greed: 50%
  - SentimentNews NLP polarity: 30%
  - Funding rate z-score proxy: 20%

Mandatory gate: social_volume_zscore >= 2.0 required to unlock scoring.
"""

from __future__ import annotations

import time
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
from atlas.providers.sentiment.alternative_me_provider import (
    SOCIAL_VOLUME_GATE_ZSCORE,
    compute_sentiment_from_fg_index,
    compute_weighted_sentiment_score,
    detect_archetype_flags,
    score_funding_rate_proxy,
    score_nlp_polarity_proxy,
)

MAX_SCORE = 35


class SentimentAgent(BaseAgent):
    """Evaluates sentiment via Alternative.me F&G, NLP polarity, and funding proxy."""

    @property
    def name(self) -> str:
        return "sentiment"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.SENTIMENT

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        start_ms = time.monotonic()
        ctx = context or {}
        vol_zscore = float(ctx.get("vol_zscore", ctx.get("social_volume_zscore", 0.0)))

        if vol_zscore < SOCIAL_VOLUME_GATE_ZSCORE:
            logger.debug(
                "sentiment_gate_blocked | vol_zscore={}",
                vol_zscore,
            )
            return self._build_gated_result(vol_zscore)

        return self._eval_metrics(ctx, vol_zscore, start_ms)

    def _eval_metrics(
        self,
        ctx: dict[str, Any],
        vol_zscore: float,
        start_ms: float,
    ) -> AgentResult:
        """Score weighted F&G, NLP, and funding components; scale to 35-point pillar."""
        fg_index = int(ctx.get("fear_greed_score", 50))
        classification = str(ctx.get("fear_greed_classification", "Neutral"))
        polarity_pct = float(ctx.get("polarity_percentile", 50.0))
        funding_z = float(ctx.get("funding_rate_zscore", ctx.get("funding_zscore", 0.0)))

        fg_output = compute_sentiment_from_fg_index(fg_index, classification)
        fg_component = fg_output.sentiment_score
        nlp_component = score_nlp_polarity_proxy(polarity_pct)
        funding_component = score_funding_rate_proxy(funding_z)

        weighted_signed = compute_weighted_sentiment_score(
            fg_component,
            nlp_component,
            funding_component,
        )
        pillar_score = min(MAX_SCORE, abs(weighted_signed))
        gate_met = vol_zscore >= SOCIAL_VOLUME_GATE_ZSCORE
        archetype_flags = detect_archetype_flags(fg_index, gate_met)

        sub_signals: dict[str, SubSignalResult] = {}
        sub_signals["fear_greed_score"] = SubSignalResult.model_construct(
            value=str(fg_index),
            flag=fg_output.flag,
        )
        sub_signals["sentiment_score"] = SubSignalResult.model_construct(
            value=str(weighted_signed),
            flag=fg_output.flag,
        )
        sub_signals["polarity_percentile"] = SubSignalResult.model_construct(
            value="{:.1f}p".format(polarity_pct),
            flag=self._polarity_flag(polarity_pct),
        )
        for archetype in archetype_flags:
            sub_signals[archetype.lower()] = SubSignalResult.model_construct(
                value="true",
                flag=archetype,
            )

        conv, risks = self._eval_convergences(fg_component, nlp_component, funding_component)
        direction = self._resolve_direction(weighted_signed)
        elapsed = (time.monotonic() - start_ms) * 1000
        return self._build_scored_result(
            pillar_score,
            direction,
            conv,
            risks,
            sub_signals,
            elapsed,
        )

    @staticmethod
    def _eval_convergences(
        fg_pts: int,
        nlp_pts: int,
        funding_pts: int,
    ) -> tuple[list[str], list[str]]:
        """Determine convergences and risks from signed components."""
        conv: list[str] = []
        risks: list[str] = []
        if abs(fg_pts) >= 25:
            conv.append("Extreme Fear & Greed baseline (Alternative.me).")
        if abs(nlp_pts) >= 20:
            conv.append("High NLP polarity consensus.")
        if abs(funding_pts) >= 20:
            conv.append("Funding-rate sentiment proxy at tail.")
        if fg_pts == 0 and nlp_pts == 0 and funding_pts == 0:
            risks.append("Sentiment metrics flat despite volume gate passing.")
        return conv, risks

    def _build_scored_result(
        self,
        score: int,
        direction: SignalDirection,
        conv: list[str],
        risks: list[str],
        sub_signals: dict[str, SubSignalResult],
        elapsed: float,
    ) -> AgentResult:
        """Assemble the scored AgentResult."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=int(min(score, MAX_SCORE)),
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation="Sentiment scores {}/{}.".format(score, MAX_SCORE),
            convergences=conv,
            risks=risks,
            veto=False,
            sub_signals=sub_signals,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )

    @staticmethod
    def _polarity_flag(pct: float) -> str:
        if pct >= 85.0:
            return "POLARITY_STRONG"
        if pct >= 65.0:
            return "POLARITY_MODERATE"
        return "POLARITY_WEAK"

    @staticmethod
    def _resolve_direction(weighted_signed: int) -> SignalDirection:
        """Map signed blend to trade direction."""
        if weighted_signed >= 12:
            return SignalDirection.BULLISH
        if weighted_signed <= -12:
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _build_gated_result(self, vol_zscore: float) -> AgentResult:
        """Return zero-score result when social volume gate blocks scoring."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=SignalDirection.NEUTRAL,
            explanation="Vol gate blocked: zscore={:.2f} < 2.0".format(vol_zscore),
            convergences=[],
            risks=["VOLUME_GATE_BLOCKED"],
            veto=False,
            sub_signals={},
            telemetry=AgentTelemetry(latency_ms=0.0),
        )


# Backward-compatible alias used by orchestrator and chaos tests.
SentimentNewsAgent = SentimentAgent
