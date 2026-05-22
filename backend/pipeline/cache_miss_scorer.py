"""Safe fallback when per-timeframe Redis scores are not yet populated."""

from __future__ import annotations

from loguru import logger

from backend.pipeline.gate_score import Direction, GateScore, Regime


class CacheMissScorer:
    """Returns a failing neutral score until the scheduler writes ``score:*`` keys."""

    async def score(self, symbol: str, timeframe: str) -> GateScore:
        logger.warning(
            "gate_score_cache_miss_scorer | symbol={} | timeframe={}",
            symbol,
            timeframe,
        )
        return GateScore(
            timeframe=timeframe,
            score=0,
            direction=Direction.NEUTRAL,
            direction_confidence=0.0,
            regime=Regime.RANGING,
        )
