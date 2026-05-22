"""HydraStreamListener — local HYDRA cascade feed via Redis Streams.

Consumes from ``atlas:stream:hydra:cascades`` and maintains an
in-memory buffer of the latest cascade event per asset.

Architecture notes:
    - HYDRA is a sibling service (read-only, isolated, Redis DB 1).
    - This listener uses Redis Streams (migrated from Pub/Sub).
    - The public API (get_latest_event, get_health_status, close)
      remains stable.
    - Heartbeat is passive: checks time.monotonic() delta on demand.
"""

import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.core.stream_consumer import StreamConsumer
from atlas.core.stream_payloads import HydraCascadeStreamPayload


class HydraCascadeEvent(BaseModel, frozen=True):
    """Immutable record of a single HYDRA liquidation cascade detection.

    Attributes:
        event_id: Unique identifier for this cascade event.
        asset: Trading pair symbol (e.g. 'BTC/USDT').
        tier: Cascade severity 1-4 (strict Literal, rejects 0 or 5+).
        exchanges: List of exchanges where liquidations were detected.
        total_liquidation_usd: Total liquidation volume in USD (Decimal).
        timestamp: UTC timestamp of the cascade detection.
    """

    event_id: str
    asset: str
    tier: Literal[1, 2, 3, 4]
    exchanges: list[str]
    total_liquidation_usd: Decimal
    timestamp: datetime
    current_1h_volatility: float | None = None
    average_30d_volatility: float | None = None


class HydraStreamListener(BaseProvider):
    """Local HYDRA cascade feed — Redis Streams.

    Migrated to Redis Streams in Session 18. Public API is stable
    across the migration so downstream agents need no rewrites.

    Args:
        redis_client: Async Redis connection (should be DB 1).
    """

    STREAM_NAME: str = "atlas:stream:hydra:cascades"
    GROUP_NAME: str = "hydra_listener_group"
    HEARTBEAT_THRESHOLD_SECONDS: float = 2.0

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        """Initialize the HYDRA stream listener.

        Args:
            redis_client: Async Redis connection.
        """
        super().__init__("hydra", redis_client, max_concurrent=1)
        self._buffer: dict[str, HydraCascadeEvent] = {}
        self._last_heartbeat: float = time.monotonic()
        self._listen_task: asyncio.Task[None] | None = None
        self._consumer = StreamConsumer(redis_client)

    async def start(self) -> None:
        """Launch the background Stream consumer task.

        Raises:
            RuntimeError: If already started.
        """
        if self._listen_task is not None:
            logger.warning("hydra listener already started")
            return
            
        await self._consumer.setup_group(self.STREAM_NAME, self.GROUP_NAME)
        
        self._listen_task = asyncio.create_task(
            self._listen_loop()
        )
        logger.info("hydra listener started | stream={}", self.STREAM_NAME)

    async def _listen_loop(self) -> None:
        """Consume from HYDRA stream and process messages.

        Runs indefinitely until cancelled. Each message is decoded
        via msgspec and validated. On success, the
        buffer is updated and heartbeat refreshed. On failure,
        the provider is marked degraded.
        """
        while True:
            try:
                messages = await self._consumer.consume(
                    stream=self.STREAM_NAME,
                    group=self.GROUP_NAME,
                    consumer="listener_loop",
                    count=10,
                    block_ms=1000,
                )
                
                for msg in messages:
                    await self._process_message(msg.payload_bytes)
                
                if messages:
                    msg_ids = [m.entry_id for m in messages]
                    await self._consumer.ack(self.STREAM_NAME, self.GROUP_NAME, *msg_ids)
                    
            except asyncio.CancelledError:
                logger.info("hydra listener loop cancelled")
                raise
            except Exception as exc:
                logger.error("hydra listen loop error | error={}", str(exc))
                await asyncio.sleep(1.0)

    async def _process_message(self, raw: bytes) -> None:
        """Decode, buffer a single stream message.

        Args:
            raw: Raw bytes from the Redis stream message payload.
        """
        try:
            payload = msgspec.json.decode(raw, type=HydraCascadeStreamPayload)
            event = payload.to_domain()
            
            self._buffer[event.asset] = event
            self._last_heartbeat = time.monotonic()
            self.mark_healthy()
        except Exception as exc:
            logger.error(
                "hydra decode failed | error={}",
                str(exc),
            )
            self.mark_degraded(str(exc))

    def get_latest_event(
        self,
        asset: str | None = None,
    ) -> HydraCascadeEvent | None:
        """Read the latest cascade event from the in-memory buffer.

        No I/O — reads from a dict populated by the listener loop.

        Args:
            asset: If provided, return the latest event for that asset.
                   If None, return the most recent event across all assets.

        Returns:
            The latest HydraCascadeEvent, or None if buffer is empty.
        """
        if asset is not None:
            return self._buffer.get(asset)
        if not self._buffer:
            return None
        return max(
            self._buffer.values(),
            key=lambda e: e.timestamp,
        )

    async def get_health_status(self) -> ProviderHealth:
        """Return a passive health snapshot based on heartbeat age.

        No background loop — simply checks the time delta between
        now and the last successful message receipt.

        Returns:
            ProviderHealth with HEALTHY or DEGRADED status.
        """
        delta = time.monotonic() - self._last_heartbeat
        status: Literal["HEALTHY", "DEGRADED"] = (
            "DEGRADED"
            if delta > self.HEARTBEAT_THRESHOLD_SECONDS
            else "HEALTHY"
        )
        return ProviderHealth(
            name="hydra",
            status=status,
            last_update=self._last_heartbeat,
            error=self._last_error,
        )

    async def close(self) -> None:
        """Cancel the background listener task and clean up.

        Safe to call multiple times.
        """
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                if not self._listen_task.cancelled():
                    raise
            self._listen_task = None
            logger.info("hydra listener closed")


def _decode_cascade_event(raw: bytes) -> HydraCascadeEvent:
    """Decode raw bytes to a validated HydraCascadeEvent.

    Uses msgspec for JSON decoding to HydraCascadeStreamPayload, then
    calls to_domain() to get the HydraCascadeEvent.

    Args:
        raw: Raw JSON bytes from Redis stream message.

    Returns:
        Validated HydraCascadeEvent instance.
    """
    payload = msgspec.json.decode(raw, type=HydraCascadeStreamPayload)
    return payload.to_domain()
