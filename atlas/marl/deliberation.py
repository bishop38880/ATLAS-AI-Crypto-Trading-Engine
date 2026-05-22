"""Shadow deliberation coordinator for MARL cooperative revision."""

from __future__ import annotations

import asyncio
from typing import Protocol

from loguru import logger

from atlas.marl.message_encoder import AgentMessage, MessageEncoder, ShadowMetrics


class DeliberationCoordinator:
    """Coordinates shadow context fetch, encoding, and revision inference."""

    def __init__(
        self,
        injector: "_ShadowFetcher",
        encoder: MessageEncoder,
        revision_head: "_RevisionHeadLike",
    ) -> None:
        self._injector = injector
        self._encoder = encoder
        self._revision_head = revision_head

    async def deliberate(
        self,
        round1_weighted_score: float,
        risk_veto: bool,
        technical_message: AgentMessage,
        asset: str,
    ) -> float:
        if risk_veto:
            return round1_weighted_score
        try:
            shadow = await self._injector.fetch(asset)
            vector = self._encoder.encode(technical_message, shadow)
            padded = _pad_vector(vector, self._revision_head.input_dim)
            revision = await asyncio.to_thread(self._revision_head.revise, padded)
            if shadow.avg_pairwise_correlation > 0.70:
                revision = max(-0.05, min(0.05, revision))
            revised = round1_weighted_score * (1.0 + revision)
            return max(0.0, revised)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("marl_deliberation_failed | asset={} | error={}", asset, exc)
            return round1_weighted_score


def _pad_vector(values: list[float], target_dim: int) -> list[float]:
    if len(values) >= target_dim:
        return values[:target_dim]
    return values + [0.0] * (target_dim - len(values))


class _ShadowFetcher(Protocol):
    async def fetch(self, asset: str) -> ShadowMetrics: ...


class _RevisionHeadLike(Protocol):
    @property
    def input_dim(self) -> int: ...

    def revise(self, features: list[float]) -> float: ...
