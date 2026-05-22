"""PythHermesConnector — SSE streaming price ingestor for Pyth Hermes.

Maintains a persistent SSE connection to the Pyth Hermes
``/v2/updates/price/stream`` endpoint and writes parsed Decimal
prices to Redis for sub-second consumption by the ValidationGate
and internal pricing engines.

Architecture notes:
    - Uses ``httpx_sse.aconnect_sse`` for the SSE transport.
    - All JSON decoding via ``msgspec.json.decode`` (never stdlib json).
    - Prices are Decimal-only; computed via ``apply_pyth_exponent``.
    - Reconnects automatically with exponential backoff on stream drops.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import httpx_sse
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.pyth.models import (
    FEED_ID_TO_ASSET,
    PYTH_FEED_IDS,
    PythPriceUpdate,
    apply_pyth_exponent,
)
from atlas.shared.config import PolarisSettings


class PythHermesConnector:
    """SSE streaming connector for Pyth Hermes price feeds.

    Subscribes to real-time price updates for all assets in
    ``PYTH_FEED_IDS`` and writes the resulting Decimal prices
    to Redis under ``atlas:price:{asset}`` keys.

    Args:
        redis_client: Injected async Redis connection.
        settings: PolarisSettings with Pyth configuration.
        http_client: Injected httpx.AsyncClient for SSE transport.
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        self._http = http_client
        self._stream_task: asyncio.Task[None] | None = None
        self._last_heartbeat: float = time.monotonic()
        self._status: str = "HEALTHY"
        self._last_error: str | None = None

    @property
    def provider_name(self) -> str:
        """Return the provider's unique identifier."""
        return "pyth_hermes"

    @property
    def status(self) -> str:
        """Return the current provider status."""
        return self._status

    def mark_degraded(self, error: str) -> None:
        """Transition provider to DEGRADED state."""
        self._status = "DEGRADED"
        self._last_error = error
        logger.warning(
            "pyth hermes degraded | error={}",
            error,
        )

    def mark_healthy(self) -> None:
        """Transition provider back to HEALTHY state."""
        if self._status != "HEALTHY":
            logger.info("pyth hermes recovered")
        self._status = "HEALTHY"
        self._last_error = None

    async def start(self) -> None:
        """Launch the background SSE consumer task."""
        if self._stream_task is not None:
            logger.warning("pyth hermes stream already started")
            return

        self._stream_task = asyncio.create_task(
            self._stream_loop()
        )
        logger.info(
            "pyth hermes stream started | feeds={}",
            len(PYTH_FEED_IDS),
        )

    async def _stream_loop(self) -> None:
        """Reconnecting SSE consumer with exponential backoff.

        Runs indefinitely until cancelled. On stream errors,
        backs off from 1s to ``pyth_reconnect_max_seconds``.
        """
        backoff = 1.0
        max_backoff = self._settings.pyth_reconnect_max_seconds

        while True:
            try:
                await self._consume_stream()
                backoff = 1.0
            except asyncio.CancelledError:
                logger.info("pyth hermes stream cancelled")
                raise
            except Exception as exc:
                self.mark_degraded(str(exc))
                logger.error(
                    "pyth stream error, reconnecting | backoff={} | error={}",
                    backoff,
                    str(exc),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def _consume_stream(self) -> None:
        """Open SSE connection and process events until disconnect."""
        url = self._build_stream_url()
        async with httpx_sse.aconnect_sse(
            self._http,
            "GET",
            url,
        ) as event_source:
            async for sse in event_source.aiter_sse():
                if sse.data:
                    await self._process_sse_data(sse.data)

    def _build_stream_url(self) -> str:
        """Construct the Hermes stream URL with all feed IDs."""
        base = self._settings.pyth_hermes_url
        ids_params = "&".join(
            "ids[]=" + fid for fid in PYTH_FEED_IDS.values()
        )
        return "{}/v2/updates/price/stream?{}&parsed=true".format(
            base, ids_params,
        )

    async def _process_sse_data(self, raw_data: str) -> None:
        """Decode an SSE data blob and write prices to Redis.

        Args:
            raw_data: The raw JSON string from the SSE event ``data`` field.
        """
        try:
            payload = msgspec.json.decode(raw_data.encode("utf-8"))
        except Exception as exc:
            logger.error("pyth decode failed | error={}", str(exc))
            return

        parsed = payload.get("parsed", []) if isinstance(payload, dict) else []
        for item in parsed:
            await self._handle_parsed_item(item)

    async def _handle_parsed_item(self, item: Any) -> None:
        """Extract price from a single parsed feed item and persist."""
        try:
            feed_id = item["id"]
            price_data = item["price"]
            update = PythPriceUpdate(
                price_id=feed_id,
                price=apply_pyth_exponent(
                    str(price_data["price"]),
                    int(price_data["expo"]),
                ),
                conf=apply_pyth_exponent(
                    str(price_data["conf"]),
                    int(price_data["expo"]),
                ),
                publish_time=price_data["publish_time"],
            )
        except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
            logger.error("pyth parse item failed | error={}", str(exc))
            return

        await self._write_to_redis(update)
        self._last_heartbeat = time.monotonic()
        self.mark_healthy()

    async def _write_to_redis(self, update: PythPriceUpdate) -> None:
        """Persist a PythPriceUpdate to Redis.

        Writes to ``atlas:price:{asset}`` with the configured TTL.
        The value is msgspec-encoded for consistency with the stack.
        """
        asset = FEED_ID_TO_ASSET.get(update.price_id)
        if asset is None:
            return

        key = "atlas:price:{}".format(asset)
        value = msgspec.json.encode({
            "price": str(update.price),
            "conf": str(update.conf),
            "publish_time": update.publish_time,
        })
        ttl = self._settings.pyth_price_ttl_seconds
        try:
            await self._redis.setex(key, ttl, value)
        except Exception as exc:
            logger.error(
                "pyth redis write failed | asset={} | error={}",
                asset,
                str(exc),
            )

    async def health_check(self) -> bool:
        """Return whether the connector is healthy."""
        return self._status == "HEALTHY"

    async def close(self) -> None:
        """Cancel the background SSE stream task and clean up."""
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                cancelling = getattr(current_task, "cancelling", lambda: 0)
                if current_task is not None and cancelling():
                    raise
            self._stream_task = None
            logger.info("pyth hermes stream closed")
