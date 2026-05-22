"""Signal Publisher — Egress to PROMETHEUS."""

import redis.asyncio as redis_async
from loguru import logger

from atlas.models.signal import SignalOutput
from atlas.shared.serialisation import encode_json


class SignalPublisher:
    """Publishes signals to the PROMETHEUS engine via Pub/Sub."""

    CHANNEL: str = "atlas:signals"

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        """Initialize the signal publisher.

        Args:
            redis_client: Async Redis connection for Pub/Sub.
        """
        self._redis = redis_client

    async def publish(self, signal: SignalOutput) -> int:
        """Encode and publish a signal to PROMETHEUS.

        Args:
            signal: The SignalOutput instance to publish.

        Returns:
            Number of subscribers that received the message.
        """
        try:
            from atlas.shared.serialisation import pydantic_to_msgspec
            payload = pydantic_to_msgspec(signal)
            receivers = await self._redis.publish(self.CHANNEL, payload)
            logger.info(
                "Published signal | id={} asset={} decision={} receivers={}",
                signal.signal_id,
                signal.asset,
                signal.decision.value,
                receivers,
            )
            return int(receivers)
        except Exception as exc:
            logger.error(
                "Failed to publish signal {} | error={}",
                signal.signal_id,
                str(exc),
            )
            return 0
