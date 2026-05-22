"""SignalStateManager — Redis-backed signal state management.

Publishes signals to ``polaris:signals:{asset}`` for PROMETHEUS
consumption and stores them with O(1) ``signal_id`` lookup.

Serialization uses ``msgspec.json.encode(signal.model_dump())`` —
never stdlib ``json``, never ``model_dump_json()``.

Architecture note:
    ATLAS holds no state in memory between cycles. All state is read
    from Redis or PostgreSQL. This module is the single write path
    for signal state.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from atlas.shared.serialisation import pydantic_to_msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.models.signal import SignalOutput


# ---------------------------------------------------------------------------
# Redis key patterns
# ---------------------------------------------------------------------------

_SIGNAL_CHANNEL_PATTERN: str = "polaris:signals:{asset}"
_SIGNAL_KEY_PATTERN: str = "signal:{signal_id}"
_SIGNAL_LATEST_KEY_PATTERN: str = "signal:latest:{asset}"


class SignalStateManager:
    """Manages signal publication and storage in Redis.

    Attributes:
        _redis: Async Redis client.
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
    ) -> None:
        """Initialize with an async Redis client.

        Args:
            redis_client: Connected redis.asyncio.Redis instance.
        """
        self._redis = redis_client

    async def publish_signal(self, signal: SignalOutput) -> int:
        """Publish a signal to the asset's pub/sub channel.

        This is the sole ATLAS → PROMETHEUS communication path.
        PROMETHEUS subscribes to ``polaris:signals:{asset}``.

        Args:
            signal: The complete SignalOutput to publish.

        Returns:
            Number of subscribers that received the message.
        """
        channel = _format_channel(signal.asset)
        payload = _serialize_signal(signal)

        subscriber_count = await self._redis.publish(channel, payload)

        logger.info(
            "signal published | signal_id={} | asset={} | channel={} | subscribers={}",
            signal.signal_id,
            signal.asset,
            channel,
            subscriber_count,
        )
        return subscriber_count

    async def store_signal(self, signal: SignalOutput) -> None:
        """Store a signal in Redis with O(1) signal_id lookup.

        Stores two keys:
        - ``signal:{signal_id}`` for direct ID lookup
        - ``signal:latest:{asset}`` for latest-signal-per-asset lookup

        Both keys expire at the signal's ``expires_at`` time.

        Args:
            signal: The complete SignalOutput to store.
        """
        payload = _serialize_signal(signal)
        ttl_seconds = _compute_ttl_seconds(signal)

        signal_key = _SIGNAL_KEY_PATTERN.format(
            signal_id=signal.signal_id,
        )
        latest_key = _SIGNAL_LATEST_KEY_PATTERN.format(
            asset=signal.asset,
        )

        await asyncio.gather(
            _store_with_expiry(self._redis, signal_key, payload, ttl_seconds),
            _store_with_expiry(self._redis, latest_key, payload, ttl_seconds),
        )

        logger.info(
            "signal stored | signal_id={} | asset={} | ttl_seconds={}",
            signal.signal_id,
            signal.asset,
            ttl_seconds,
        )

    async def publish_and_store(self, signal: SignalOutput) -> int:
        """Publish and store a signal in a single call.

        Convenience method that calls both publish_signal and
        store_signal.

        Args:
            signal: The complete SignalOutput.

        Returns:
            Number of subscribers that received the message.
        """
        subscriber_count = await self.publish_signal(signal)
        await self.store_signal(signal)
        return subscriber_count


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def _serialize_signal(signal: SignalOutput) -> bytes:
    """Serialize a SignalOutput to bytes via msgspec.

    Uses ``msgspec.json.encode(signal.model_dump(mode='json'))``
    to ensure Decimal fields serialize as strings (not floats)
    and datetime fields serialize as ISO 8601 strings.

    Args:
        signal: The SignalOutput to serialize.

    Returns:
        JSON bytes ready for Redis.
    """
    return pydantic_to_msgspec(signal)


def _format_channel(asset: str) -> str:
    """Format the Redis pub/sub channel name for an asset.

    Args:
        asset: Trading pair (e.g. "BTCUSDT").

    Returns:
        Channel name (e.g. "polaris:signals:BTCUSDT").
    """
    return _SIGNAL_CHANNEL_PATTERN.format(asset=asset)


def _compute_ttl_seconds(signal: SignalOutput) -> int:
    """Compute Redis key TTL from signal expiry.

    Args:
        signal: Signal with expires_at and timestamp.

    Returns:
        TTL in seconds (minimum 1).
    """
    delta = signal.expires_at - signal.timestamp
    ttl = int(delta.total_seconds())
    return max(ttl, 1)


async def _store_with_expiry(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    key: str,
    value: bytes,
    ttl_seconds: int,
) -> None:
    """Store a value in Redis with expiry.

    Args:
        redis_client: Async Redis client.
        key: Redis key.
        value: Serialized payload.
        ttl_seconds: Time-to-live in seconds.
    """
    await redis_client.setex(key, ttl_seconds, value)
