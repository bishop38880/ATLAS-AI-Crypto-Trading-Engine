"""Message encoding for MARL shadow deliberation."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from atlas.agents.base import SignalDirection


class AgentMessage(BaseModel):
    """Immutable agent payload consumed by MARL shadow inference."""

    model_config = ConfigDict(frozen=True)

    agent_name: str = Field(min_length=1)
    score: int = Field(ge=0, le=220)
    max_score: int = Field(gt=0, le=220)
    direction: SignalDirection = SignalDirection.NEUTRAL
    risk_veto: bool = False


class ShadowMetrics(BaseModel):
    """Shadow-only market context used by the revision head."""

    model_config = ConfigDict(frozen=True)

    avg_pairwise_correlation: float = 0.0
    volatility_zscore: float = 0.0


class MessageEncoder:
    """Encodes agent messages into compact neural features."""

    def _infer_direction(self, direction: SignalDirection) -> float:
        if direction == SignalDirection.BULLISH:
            return 1.0
        if direction == SignalDirection.BEARISH:
            return -1.0
        return 0.0

    def encode(self, message: AgentMessage, shadow: ShadowMetrics) -> list[float]:
        score_ratio = message.score / message.max_score
        confidence = min(1.0, max(0.0, score_ratio))
        veto_flag = 1.0 if message.risk_veto else 0.0
        return [
            score_ratio,
            confidence,
            self._infer_direction(message.direction),
            shadow.avg_pairwise_correlation,
            veto_flag,
        ]

    def encode_all(
        self, messages: list[AgentMessage], shadow: ShadowMetrics,
    ) -> list[list[float]]:
        return [self.encode(message, shadow) for message in messages]
