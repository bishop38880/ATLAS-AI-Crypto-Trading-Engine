"""Staged position exits — ATLAS publishes closes; PROMETHEUS executes on Kraken."""

from __future__ import annotations

import msgspec
from loguru import logger
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from backend.config.pipeline_config import PROMETHEUS_STAGED_EXIT_CHANNEL


class StagedExitRequest(BaseModel, frozen=True):
    """Outbound close instruction for PROMETHEUS."""

    order_id: str = Field(description="PROMETHEUS order id for the open position")
    symbol: str
    direction: str = Field(description="LONG or SHORT")
    close_pct: float = Field(ge=0.0, le=1.0, description="Fraction of position to close")
    reason: str
    exit_kind: str = Field(description="partial | full")


class PrometheusStagedExitBridge:
    """Risk-governor bridge — ATLAS never submits exchange orders directly."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    async def partial_close(
        self,
        *,
        order_id: str,
        symbol: str,
        direction: str,
        close_pct: float,
        reason: str,
    ) -> None:
        await self._publish_close(
            StagedExitRequest(
                order_id=order_id,
                symbol=symbol.upper(),
                direction=direction.upper(),
                close_pct=close_pct,
                reason=reason,
                exit_kind="partial",
            ),
        )

    async def full_close(
        self,
        *,
        order_id: str,
        symbol: str,
        direction: str,
        reason: str,
    ) -> None:
        await self._publish_close(
            StagedExitRequest(
                order_id=order_id,
                symbol=symbol.upper(),
                direction=direction.upper(),
                close_pct=1.0,
                reason=reason,
                exit_kind="full",
            ),
        )

    async def _publish_close(self, request: StagedExitRequest) -> None:
        payload = msgspec.json.encode(request.model_dump()).decode("utf-8")
        try:
            await self._redis.publish(PROMETHEUS_STAGED_EXIT_CHANNEL, payload)
            logger.info(
                "staged_exit_published | symbol={} | kind={} | close_pct={}",
                request.symbol,
                request.exit_kind,
                request.close_pct,
            )
        except Exception as exc:
            logger.exception(
                "staged_exit_publish_failed | symbol={} | err={}",
                request.symbol,
                str(exc),
            )
