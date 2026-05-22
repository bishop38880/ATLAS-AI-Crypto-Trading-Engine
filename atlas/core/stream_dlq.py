"""Dead Letter Queue background task for Redis Streams."""

import redis.asyncio as redis_async
from loguru import logger

from atlas.shared.config import PolarisSettings


class StreamDLQ:
    """Processes dead letters for a Redis Stream."""

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client
        self._settings = PolarisSettings()

    async def process_dead_letters(self, stream: str, group: str) -> int:
        """Claim and acknowledge messages pending longer than threshold."""
        try:
            threshold_ms = self._settings.dead_letter_threshold_seconds * 1000
            results = await self._redis.xautoclaim(
                name=stream,
                groupname=group,
                consumername="dlq_processor",
                min_idle_time=threshold_ms,
                start_id="0-0",
                count=100,
            )
            if not results or not results[1]:
                return 0
                
            return await self._handle_claimed_messages(stream, group, results[1])
        except Exception as exc:
            logger.error("DLQ processing failed | error={}", str(exc))
            return 0

    async def _handle_claimed_messages(
        self, stream: str, group: str, messages: list[tuple[bytes, dict[bytes, bytes]]]
    ) -> int:
        """Log and acknowledge claimed dead letters."""
        msg_ids = []
        for msg_id, _ in messages:
            msg_id_str = (
                msg_id.decode("utf-8")
                if isinstance(msg_id, bytes)
                else str(msg_id)
            )
            msg_ids.append(msg_id_str)
            logger.warning(
                "Dead letter detected and claimed | stream={} group={} id={}",
                stream, group, msg_id_str,
            )

        if msg_ids:
            await self._redis.xack(stream, group, *msg_ids)
            logger.info("Acknowledged {} dead letters on stream {}", len(msg_ids), stream)
        return len(msg_ids)
