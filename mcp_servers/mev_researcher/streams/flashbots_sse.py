"""Optional Flashbots MEV-Share SSE enrichment scaffold."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import msgspec.json
from loguru import logger

from ..coordinator import SurveillanceCoordinator


async def flashbots_sse_ping_loop(
    *,
    stop_evt: asyncio.Event,
    coordinator: SurveillanceCoordinator,
    sse_endpoint: str,
) -> None:
    """
    Long-lived chunked reader for SSE endpoints.

    Section 25.5 Architecture — enrichment must stay outside MCP tool calls.
    """

    trimmed: str = sse_endpoint.strip()

    if trimmed == "":

        logger.info("FLASHBOTS_MEV_SHARE_SSE empty; enrichment idle")

        await coordinator.note_flashbots_health(degraded=True)

        await stop_evt.wait()

        return

    timeout_configuration = httpx.Timeout(3600.0, connect=20.0, read=None)

    while not stop_evt.is_set():

        try:

            async with httpx.AsyncClient(timeout=timeout_configuration, http2=True) as client:

                await _consume_stream_once(
                    http_client=client,
                    sse_url=trimmed,
                )

                await coordinator.note_flashbots_health(degraded=False)

        except asyncio.CancelledError:

            raise

        except Exception as exc:

            logger.warning("Flashbots SSE degraded | backoff | err={}", exc)

            await coordinator.note_flashbots_health(degraded=True)

            await asyncio.sleep(20.0)

    logger.info("Flashbots SSE task exiting")


async def _consume_stream_once(
    *,
    http_client: httpx.AsyncClient,
    sse_url: str,
) -> None:
    """Read until remote closes SSE channel."""

    async with http_client.stream(
        "GET",
        sse_url,
        headers={"Accept": "text/event-stream"},
    ) as streamed:

        streamed.raise_for_status()

        async for textual_chunk in streamed.aiter_text():

            trimmed_chunk: str = textual_chunk.strip()

            if trimmed_chunk == "":

                continue

            _maybe_log_sse_json(trimmed_chunk)


def _maybe_log_sse_json(chunk_tail: str) -> None:

    fragments: list[str] = chunk_tail.splitlines()

    if not fragments:
        return

    candidate_payload: str = fragments[-1].removeprefix("data:").strip()

    if candidate_payload in ("", "[DONE]", "ping"):
        return

    try:

        _ = msgspec.json.decode(candidate_payload.encode())

        logger.trace("Flashbots SSE structured frame buffered")

    except msgspec.DecodeError:

        logger.debug("Flashbots SSE chunk ignored | len={}", len(candidate_payload))
