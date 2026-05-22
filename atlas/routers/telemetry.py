"""Telemetry streaming endpoints."""

import asyncio
from typing import AsyncGenerator

import redis.asyncio as redis_asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loguru import logger

from atlas.shared.config import PolarisSettings

router = APIRouter(tags=["telemetry"])

CHANNEL = "atlas:telemetry:stream"


async def event_generator() -> AsyncGenerator[str, None]:
    """Yield SSE telemetry logs by subscribing to Redis Pub/Sub."""
    settings = PolarisSettings()
    redis_client = redis_asyncio.from_url(settings.redis_url)
    pubsub = redis_client.pubsub()

    await pubsub.subscribe(CHANNEL)
    try:
        async for msg in pubsub.listen():
            if msg["type"] != "message":
                continue
            raw = msg["data"]
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            yield f"data: {text}\n\n"
    except asyncio.CancelledError:
        logger.info("Telemetry SSE connection cancelled by client")
        raise
    finally:
        await pubsub.unsubscribe(CHANNEL)
        await pubsub.aclose()
        await redis_client.aclose()


@router.get("/api/telemetry/stream")
async def telemetry_stream() -> StreamingResponse:
    """Stream real-time AI Activity telemetry to frontend via SSE."""
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
