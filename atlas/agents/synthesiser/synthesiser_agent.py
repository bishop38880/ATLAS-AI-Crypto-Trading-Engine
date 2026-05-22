"""Synthesiser Agent — Tier 3 final output generation."""
import asyncio
from decimal import Decimal
from typing import Any, TYPE_CHECKING
import numpy as np

from atlas.agents.base import AgentCategory, AgentResult, AgentTier
from atlas.models.signal import SignalOutput
from atlas.ml.calibrator import ConvictionCalibrator
from loguru import logger

if TYPE_CHECKING:
    from atlas.orchestrator.scorer import ConfluenceScorer

class SynthesiserAgent:
    """Synthesiser Agent that aggregates Analyst results into a final SignalOutput."""

    def __init__(self, scorer: "ConfluenceScorer", calibrator: ConvictionCalibrator | None = None) -> None:
        self._scorer = scorer
        self._calibrator = calibrator

    @property
    def name(self) -> str:
        return "synthesiser"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.CONTEXT

    @property
    def tier(self) -> AgentTier:
        return AgentTier.SYNTHESISER

    async def synthesize(
        self,
        analyst_results: list[AgentResult],
        position_size: Decimal,
        asset: str,
        timeframe: str = "30m",
        correlation_matrix: np.ndarray | None = None,
        agent_names: list[str] | None = None,
        cycle_id: str = "",
        cycle_latency_ms: float = 0.0,
    ) -> SignalOutput:
        """Apply ML models and generate final signal."""
        
        signal = await self._scorer.score(
            agent_results=analyst_results,
            asset=asset,
            timeframe=timeframe,
            correlation_matrix=correlation_matrix,
            agent_names=agent_names,
            cycle_id=cycle_id,
            cycle_latency_ms=cycle_latency_ms,
        )
        calibrated_prob = None
        ece_val = None
        
        if self._calibrator is not None:
            calibrated_prob = await asyncio.to_thread(self._calibrator.calibrate, float(signal.score))
            ece_val = getattr(self._calibrator, "last_ece", 0.0)
            logger.info("signal_calibrated | conviction={} | calibrated={} | ece={}", signal.score, calibrated_prob, ece_val)
        else:
            calibrated_prob = await self._apply_isotonic_calibration(signal.score)
        
        return signal.model_copy(update={
            "calibrated_probability": calibrated_prob,
            "calibration_ece": ece_val if ece_val else None,
            # Position size is passed to Synthesiser but PROMETHEUS is responsible 
            # for sizing per Session 00 hard wall. It acts as a hint/context.
        })

    async def _apply_isotonic_calibration(self, raw_score: int) -> float:
        """Apply isotonic calibration (mock). Wrapped in to_thread."""
        def _calibrate() -> float:
            return min(1.0, max(0.0, float(raw_score) / 100.0 * 0.95))
        return await asyncio.to_thread(_calibrate)
