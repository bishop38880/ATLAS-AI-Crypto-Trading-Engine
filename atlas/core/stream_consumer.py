"""Redis Streams Consumer with Group Support."""

from dataclasses import dataclass

import redis.asyncio as redis_async
from loguru import logger
from redis.exceptions import ResponseError


@dataclass(frozen=True)
class StreamMessage:
    stream: str
    entry_id: str
    payload_bytes: bytes


class StreamConsumer:
    """Consumes messages from a durable Redis Stream."""

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    async def setup_group(self, stream: str, group: str) -> None:
        """XGROUP CREATE, handle BusyGroupError idempotently."""
        try:
            await self._redis.xgroup_create(stream, group, id="$", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 100,
    ) -> list[StreamMessage]:
        """XREADGROUP with blocking, returns messages for caller-side XACK."""
        try:
            results = await self._redis.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">"},
                count=count,
                block=block_ms,
            )
            return self._parse_results(results)
        except Exception as exc:
            logger.error("Consumer read failed | error={}", str(exc))
            return []

    async def consume_latest(self, stream: str) -> StreamMessage | None:
        """XREVRANGE for agents that only need current state (no group semantics)."""
        try:
            results = await self._redis.xrevrange(
                stream, max="+", min="-", count=1,
            )
            if not results:
                return None
            msg_id, raw_data = results[0]
            message_id = (
                msg_id.decode("utf-8")
                if isinstance(msg_id, bytes)
                else str(msg_id)
            )
            data_bytes = raw_data.get(b"data", b"{}")
            return StreamMessage(
                stream=stream,
                entry_id=message_id,
                payload_bytes=data_bytes,
            )
        except Exception as exc:
            logger.error(
                "Failed to read latest from {} | error={}",
                stream,
                str(exc),
            )
            return None

    async def ack(self, stream: str, group: str, *entry_ids: str) -> None:
        """Acknowledge messages after successful processing."""
        if entry_ids:
            await self._redis.xack(stream, group, *entry_ids)

    def _parse_results(self, results: list[tuple[bytes, list[tuple[bytes, dict[bytes, bytes]]]]]) -> list[StreamMessage]:  # type: ignore
        """Parse raw XREADGROUP results into StreamMessages."""
        messages = []
        for stream_bytes, items in results:
            stream = (
                stream_bytes.decode("utf-8")
                if isinstance(stream_bytes, bytes)
                else str(stream_bytes)
            )
            for msg_id, raw_data in items:
                message_id = (
                    msg_id.decode("utf-8")
                    if isinstance(msg_id, bytes)
                    else str(msg_id)
                )
                data_bytes = raw_data.get(b"data", b"{}")
                messages.append(
                    StreamMessage(
                        stream=stream,
                        entry_id=message_id,
                        payload_bytes=data_bytes,
                    )
                )
        return messages
