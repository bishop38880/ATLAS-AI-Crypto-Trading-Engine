import asyncio
import msgspec
import redis.asyncio as redis_async
from loguru import logger
from typing import Any

class RedisSignalPublisher:
    """Wrapper around redis.asyncio publish for serializing messages."""

    def __init__(self, redis_client: redis_async.Redis) -> None:
        self._redis = redis_client

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        """Serialize a dict with msgspec and publish to a Redis channel.
        
        Args:
            channel: Redis channel name.
            message: Message payload as dictionary.
        """
        try:
            payload = msgspec.json.encode(message)
            await asyncio.wait_for(
                self._redis.publish(channel, payload), timeout=5.0
            )
        except Exception as e:
            logger.error("redis publish failed | channel={} | err={}", channel, str(e))
