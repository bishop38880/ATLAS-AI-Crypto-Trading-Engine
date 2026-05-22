"""Agent base types — BaseAgent and lifecycle management.

Every ATLAS agent returns an ``AgentResult`` after processing its
data sources. The ``direction`` field encodes the agent's directional
conviction, which the ConfluenceScorer aggregates to determine the
final buy/sell decision.
"""

from __future__ import annotations

from typing import Any
from abc import ABC, abstractmethod


from loguru import logger

from atlas.models.signal import AgentResult, SignalDirection, AgentState, AgentTier, AgentCategory, AgentTelemetry

class BaseAgent(ABC):
    """Base class for all ATLAS intelligence agents."""

    MIN_SAMPLES_TO_EMIT: int = 1

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)

    def __init__(self, **kwargs: object) -> None:
        self._state: AgentState = AgentState.WARMING_UP
        self._sample_count: int = 0

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def sample_count(self) -> int:
        return self._sample_count

    @property
    def is_ready(self) -> bool:
        return self._state == AgentState.READY

    def record_sample(self) -> None:
        self._sample_count += 1
        if self._state == AgentState.WARMING_UP and self._sample_count >= self.MIN_SAMPLES_TO_EMIT:
            self._state = AgentState.READY
            logger.info("agent_ready | agent={} | samples={}", self.name, self._sample_count)

    def mark_degraded(self, reason: str) -> None:
        self._state = AgentState.DEGRADED
        logger.warning("agent_degraded | agent={} | reason={}", self.name, reason)

    def mark_failed(self, reason: str) -> None:
        self._state = AgentState.FAILED
        logger.error("agent_failed | agent={} | reason={}", self.name, reason)

    def mark_recovered(self) -> None:
        if self._state == AgentState.DEGRADED:
            self._state = AgentState.READY
            logger.info("agent_recovered | agent={}", self.name)

    @property
    @abstractmethod
    def name(self) -> str:
        pass

    @property
    @abstractmethod
    def category(self) -> AgentCategory:
        pass

    @property
    @abstractmethod
    def tier(self) -> AgentTier:
        pass

    @abstractmethod
    async def score(self, data: dict[str, Any], context: dict[str, Any] | None = None) -> AgentResult:
        pass

    def _make_zero_result(self, reason: str) -> AgentResult:
        return AgentResult.model_construct(
            agent_name=self.name, score=0, max_score=0, weight=0.0,
            direction=SignalDirection.NEUTRAL, explanation=reason,
            convergences=[], risks=[], veto=False
        )

    def _make_warmup_result(self) -> AgentResult:
        return AgentResult.model_construct(
            agent_name=self.name, score=0, max_score=0, weight=0.0,
            direction=SignalDirection.NEUTRAL,
            explanation="WARMING_UP: {}/{} samples".format(self._sample_count, self.MIN_SAMPLES_TO_EMIT),
            convergences=[], risks=["AGENT_WARMING_UP"], veto=False
        )
