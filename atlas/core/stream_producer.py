"""Redis Streams Producer for durable data ingestion."""

import msgspec
import redis.asyncio as redis_async
from loguru import logger


class StreamProducer:
    """XADD wrapper with approximate trimming."""

    STREAM_NAMES = {
        "hydra_cascades": "atlas:stream:hydra:cascades",
        "hydra_price": "atlas:stream:hydra:price",
        "defillama_tvl": "atlas:stream:defillama:tvl",
    }

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    async def publish(
        self,
        stream_key: str,
        payload: msgspec.Struct,
        maxlen: int = 10_000,
    ) -> str:
        """Publish with approximate trimming (~ MAXLEN). Returns stream entry ID."""
        try:
            encoded = msgspec.json.encode(payload)
            entry_id = await self._redis.xadd(
                stream_key,
                {"data": encoded},  # type: ignore[arg-type]
                maxlen=maxlen,
                approximate=True,
            )
            return entry_id.decode() if isinstance(entry_id, bytes) else entry_id
        except Exception as exc:
            logger.error(
                "Failed to publish to stream {} | error={}",
                stream_key,
                str(exc),
            )
            raise RuntimeError(f"Stream publish failed: {exc}") from exc
