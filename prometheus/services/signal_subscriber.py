"""Validated ATLAS signal subscriber for PROMETHEUS execution paths."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.shared.signal_guard import (
    decode_signal_payload,
    validate_pre_execution_signal,
)


SignalHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AtlasSignalSubscriber:
    """Subscribe to ATLAS signals and fail closed before execution callbacks."""

    def __init__(
        self,
        redis: redis_asyncio.Redis,  # type: ignore[type-arg]
        handler: SignalHandler,
        channel_pattern: str = "polaris:signals:*",
    ) -> None:
        self._redis = redis
        self._handler = handler
        self._channel_pattern = channel_pattern

    async def run(self) -> None:
        """Listen for ATLAS signals until cancelled."""
        pubsub = self._redis.pubsub()
        await asyncio.wait_for(
            pubsub.psubscribe(self._channel_pattern),
            timeout=5.0,
        )
        logger.info("atlas_signal_subscriber_started | pattern={}", self._channel_pattern)

        try:
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            logger.info("atlas_signal_subscriber_cancelled")
            await asyncio.wait_for(
                pubsub.punsubscribe(self._channel_pattern),
                timeout=5.0,
            )
            raise

    async def _handle_message(self, message: dict[str, Any]) -> None:
        """Decode and validate a single Redis pub/sub signal message."""
        payload = decode_signal_payload(message.get("data", b""))
        if payload is None:
            return

        if not validate_pre_execution_signal(payload):
            return

        await self._handler(payload)
