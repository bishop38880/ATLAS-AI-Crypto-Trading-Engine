"""Multi-timeframe gate chain — Daily → 4hr → 1hr → 15min → 5min."""

from __future__ import annotations

from typing import Any, Protocol

import msgspec
from loguru import logger
from redis.asyncio import Redis

from backend.config.pipeline_config import (
    GATE_CONFIGS,
    TIMEFRAME_CONFLUENCE_BONUS,
    TIMEFRAME_CONFLICT_PENALTY,
    TIMEFRAME_ORDER,
    score_cache_key,
)
from backend.pipeline.gate_score import (
    Direction,
    GateChainResult,
    GateScore,
    Regime,
    gate_score_from_cache,
)


class GateScoringEngine(Protocol):
    """Compute or supply per-timeframe confluence scores."""

    async def score(self, symbol: str, timeframe: str) -> GateScore:
        """Return a fresh GateScore for the asset and timeframe."""
        ...


class GateChain:
    """Sequential multi-timeframe gate evaluator."""

    def __init__(
        self,
        redis_client: Redis,
        scoring_engine: GateScoringEngine,
    ) -> None:
        self._redis = redis_client
        self._scorer = scoring_engine

    async def evaluate(self, symbol: str) -> GateChainResult:
        """Run all gates; stop at the first failure."""
        result = GateChainResult(symbol=symbol.upper())
        gates: dict[str, GateScore] = {}
        daily_direction: Direction | None = None

        for timeframe in TIMEFRAME_ORDER:
            config = GATE_CONFIGS[timeframe]
            gate_score = await self._score_timeframe(symbol, timeframe)
            passed, reason = self._evaluate_gate(
                gate_score,
                config,
                daily_direction,
                timeframe,
            )
            updated = gate_score.model_copy(
                update={
                    "passed": passed,
                    "fail_reason": None if passed else reason,
                },
            )
            gates[timeframe] = updated

            if not passed:
                logger.info(
                    "gate_chain_failed | symbol={} | gate={} | reason={}",
                    symbol,
                    timeframe,
                    reason,
                )
                return GateChainResult(
                    symbol=result.symbol,
                    gates=gates,
                    passed=False,
                    fail_at_gate=timeframe,
                    fail_reason=reason,
                )

            if timeframe == "daily":
                daily_direction = updated.direction

        primary = gates["1hr"]
        bonus = self._compute_timeframe_bonus(gates)
        final_score = min(220, primary.score + bonus)

        logger.info(
            "gate_chain_passed | symbol={} | final_score={} | direction={} | bonus={}",
            symbol,
            final_score,
            primary.direction.value,
            bonus,
        )
        return GateChainResult(
            symbol=result.symbol,
            gates=gates,
            passed=True,
            final_score=final_score,
            final_direction=primary.direction,
            timeframe_bonus=bonus,
        )

    async def _score_timeframe(self, symbol: str, timeframe: str) -> GateScore:
        cache_key = score_cache_key(symbol, timeframe)
        try:
            cached = await self._redis.get(cache_key)
        except Exception as exc:
            logger.warning(
                "gate_cache_read_failed | key={} | err={}",
                cache_key,
                str(exc),
            )
            cached = None

        if cached:
            data = msgspec.json.decode(cached, type=dict)
            return gate_score_from_cache(data, timeframe)

        logger.debug(
            "gate_cache_miss | symbol={} | timeframe={}",
            symbol,
            timeframe,
        )
        return await self._scorer.score(symbol, timeframe)

    def _evaluate_gate(
        self,
        gate: GateScore,
        config: Any,
        daily_direction: Direction | None,
        timeframe: str,
    ) -> tuple[bool, str]:
        if gate.score < config.min_score:
            return False, (
                "Score {} below minimum {} for {} gate".format(
                    gate.score,
                    config.min_score,
                    timeframe,
                )
            )

        if config.requires_trend and gate.regime == Regime.RANGING:
            return False, (
                "Market is RANGING on {} — daily gate requires TRENDING or TRANSITIONAL".format(
                    timeframe,
                )
            )

        if gate.direction_confidence < config.min_direction_confidence:
            return False, (
                "Direction confidence {:.0%} below minimum {:.0%} for {}".format(
                    gate.direction_confidence,
                    config.min_direction_confidence,
                    timeframe,
                )
            )

        if gate.direction == Direction.NEUTRAL:
            return False, "No clear directional bias on {}".format(timeframe)

        if daily_direction is not None and timeframe != "daily":
            if gate.direction != daily_direction:
                return False, (
                    "{} direction {} conflicts with daily direction {}".format(
                        timeframe,
                        gate.direction.value,
                        daily_direction.value,
                    )
                )

        return True, ""

    def _compute_timeframe_bonus(self, gates: dict[str, GateScore]) -> int:
        major = ("daily", "4hr", "1hr")
        directions = {gates[tf].direction for tf in major if tf in gates}
        if len(directions) == 1 and Direction.NEUTRAL not in directions:
            return TIMEFRAME_CONFLUENCE_BONUS
        if len(directions) > 1:
            return TIMEFRAME_CONFLICT_PENALTY
        return 0
