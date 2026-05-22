"""GNNAgent — shadow-only, zero-weight observer agent.

Registered in the FINCON hierarchy as a shadow agent that contributes
NOTHING to the live confluence score. Returns AgentResult with
score=0, weight=0.0. Internally runs ShadowGNNScorer for empirical
data collection.

SHADOW MODE ONLY — no live scoring until explicit human approval.
"""

from __future__ import annotations

from typing import Any

import asyncpg
import redis.asyncio as redis
from loguru import logger

from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentTelemetry,
    AgentTier,
    BaseAgent,
    SignalDirection,
)
from atlas.agents.gnn.shadow_scorer import ShadowGNNScorer, ShadowScoreResult
from atlas.shared.config import PolarisSettings


class GNNAgent(BaseAgent):
    """Shadow-only GNN agent — zero-weight observer.

    Runs the full GNN pipeline (GraphSAGE + GAT + GIN +
    MultiGraphCrossAttention) in shadow mode. All scores are
    logged and persisted but NEVER contribute to the live
    confluence score.

    The agent always returns score=0, weight=0.0 to guarantee
    zero impact on live trading decisions.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        redis_client: redis.Redis,
        settings: PolarisSettings | None = None,
    ) -> None:
        super().__init__()
        self._settings = settings or PolarisSettings()
        self._scorer = ShadowGNNScorer(
            settings=self._settings, pool=pool, redis_client=redis_client
        )

    @property
    def name(self) -> str:
        """Unique agent identifier."""
        return "gnn_shadow"

    @property
    def category(self) -> AgentCategory:
        """Agent category — correlation analysis via GNN."""
        return AgentCategory.CORRELATION

    @property
    def tier(self) -> AgentTier:
        """Agent tier — analyst level, but shadow-only."""
        return AgentTier.ANALYST

    async def score(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Return zero-weight result; run shadow scorer internally.

        data is accepted for interface compatibility with BaseAgent but
        not used — the GNN agent builds its own graph structures
        from upstream data sources via ShadowGNNScorer.
        """
        asset = self._extract_asset(context)
        shadow_result = await self._run_shadow_scoring(asset)
        self.record_sample()
        return self._build_zero_result(asset, shadow_result)

    def _extract_asset(
        self,
        context: dict[str, Any] | None,
    ) -> str:
        """Extract asset identifier from context dict."""
        if context and "asset" in context:
            return str(context["asset"])
        return "UNKNOWN"

    async def _run_shadow_scoring(
        self,
        asset: str,
    ) -> ShadowScoreResult | None:
        """Attempt shadow scoring — failures are non-fatal."""
        try:
            result = await self._scorer.score_cycle(asset=asset)
            logger.info(
                "gnn_shadow_complete | asset={} | fused={}",
                asset,
                result.fused_shadow_score,
            )
            return result
        except Exception:
            logger.warning(
                "gnn_shadow_failed | asset={}",
                asset,
            )
            return None

    def _build_zero_result(
        self,
        asset: str,
        shadow: ShadowScoreResult | None,
    ) -> AgentResult:
        """Build AgentResult with score=0, weight=0.0 (shadow only)."""
        explanation = "SHADOW_MODE: GNN scores collected, not applied."
        if shadow is not None:
            explanation = (
                "SHADOW_MODE: fused={:.4f} sage={:.4f} "
                "gat={:.4f} gin={:.4f}"
            ).format(
                shadow.fused_shadow_score,
                shadow.graphsage_score,
                shadow.gat_score,
                shadow.gin_score,
            )
        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=0,
            weight=0.0,
            direction=SignalDirection.NEUTRAL,
            explanation=explanation,
            convergences=[],
            risks=[],
            veto=False,
            telemetry=AgentTelemetry(latency_ms=0.0),
        )
