"""Solana ``logsSubscribe`` ingestion for PROMETHEUS watchlists."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import msgspec.json
from loguru import logger
from solders.pubkey import Pubkey
from websockets.asyncio.client import connect as websocket_connect

from ..cache import PoolRingBuffers
from ..coordinator import SurveillanceCoordinator
from ..models import PendingMempoolEvent
from .evm_surveillance import utc_millis_now


def _validated_pubkey(candidate: str) -> str | None:
    """Return canonical base58 pubkey or ``None``."""

    trimmed: str = candidate.strip()

    try:
        _ = Pubkey.from_string(trimmed)

        return trimmed

    except Exception:
        logger.debug("Discarded malformed Solana pubkey | value={}", candidate)

        return None


async def handshake_logs_subscription(
    *,
    websocket: Any,
    request_identifier: int,
    pubkey_ascii: str,
    server_subscription_to_pool: dict[int, str],
) -> None:
    """Await subscription acknowledgement and map RPC subscription id."""

    envelope: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_identifier,
        "method": "logsSubscribe",
        "params": [
            {"mentions": [pubkey_ascii]},
            {"commitment": "processed"},
        ],
    }

    await websocket.send(msgspec.json.encode(envelope).decode("utf-8"))

    while True:

        inbound = await asyncio.wait_for(
            websocket.recv(),
            timeout=90.0,
        )

        text_frame = inbound.decode("utf-8") if isinstance(inbound, bytes) else str(inbound)

        decoded_candidate: dict[str, Any]

        try:
            decoded_candidate = msgspec.json.decode(text_frame.encode())

        except msgspec.DecodeError:
            logger.debug("Sol handshake frame unreadable")

            continue

        if decoded_candidate.get("id") != request_identifier:
            continue

        rpc_result_candidate: Any = decoded_candidate.get("result")

        if isinstance(rpc_result_candidate, int):

            server_subscription_to_pool[int(rpc_result_candidate)] = pubkey_ascii

            return

        logger.warning(
            "logsSubscribe acknowledgement missing numeric result | pubkey={}",
            pubkey_ascii[:6],
        )

        return


async def bootstrap_sol_watchers(
    *,
    websocket: Any,
    coordinator: SurveillanceCoordinator,
    server_subscription_to_pool: dict[int, str],
) -> None:
    """Handshake each watched pool into an isolated subscription."""

    watchers: set[str] = await coordinator.solana_watch_addresses()

    sanitized: list[str] = []

    for raw_watcher in watchers:

        validated = _validated_pubkey(raw_watcher)

        if validated:

            sanitized.append(validated)

    server_subscription_to_pool.clear()

    request_anchor: int = 100

    for pubkey_ascii in sanitized:

        request_anchor += 1

        await handshake_logs_subscription(
            websocket=websocket,
            request_identifier=request_anchor,
            pubkey_ascii=pubkey_ascii,
            server_subscription_to_pool=server_subscription_to_pool,
        )


async def hydrate_logs_notification(
    *,
    decoded: dict[str, Any],
    server_subscription_to_pool: dict[int, str],
    coordinator: SurveillanceCoordinator,
    buffers: PoolRingBuffers,
) -> None:

    rpc_method_candidate: Any = decoded.get("method")

    if rpc_method_candidate != "logsNotification":
        return

    params_block = decoded.get("params")

    if not isinstance(params_block, dict):

        return

    context = params_block.get("result")

    if not isinstance(context, dict):

        return

    signature_candidate: Any = context.get("signature")

    logs_lines: Any = context.get("logs", [])

    subscription_reference: Any = context.get("subscription")

    tracked_pool: str | None = None

    if isinstance(subscription_reference, int):

        tracked_pool = server_subscription_to_pool.get(int(subscription_reference))

    if tracked_pool is None:

        watchers = await coordinator.solana_watch_addresses()

        trimmed_logs = ""

        if isinstance(logs_lines, list):

            trimmed_logs = " ".join(str(line) for line in logs_lines)

        watcher_match: str | None = None

        for watcher_pubkey in watchers:

            lowered = watcher_pubkey.strip()

            if lowered and lowered in trimmed_logs:

                watcher_match = watcher_pubkey.strip()

                break

        tracked_pool = watcher_match

        if tracked_pool is None:

            return

    pool_literal = tracked_pool.strip()

    if not isinstance(signature_candidate, str):

        return

    removal_signal = classify_log_drain_candidate(logs_lines)

    heuristic_complexity = estimate_instruction_complexity(logs_lines)

    bias_guess = guess_bias_from_logs(logs_lines)

    notional_guess = estimate_sol_notional()

    pending_event = PendingMempoolEvent(
        chain="solana",
        pool_address=pool_literal,
        tx_signature=signature_candidate,
        direction_bias=bias_guess,
        notional_usd=notional_guess,
        priority_fee_micros=estimate_priority_proxy(logs_lines),
        complexity_score=heuristic_complexity,
        captured_at_unix_ms=utc_millis_now(),
        liquidity_removal_candidate=removal_signal,
    )

    await buffers.record_event(pending_event)


def classify_log_drain_candidate(logs_lines: Any) -> bool:
    """Rough liquidity drain detection from textual logs."""

    if not isinstance(logs_lines, list):
        return False

    stitched: str = " ".join(str(line).upper() for line in logs_lines)

    needle_markers = (
        "REMOVE LIQUIDITY",
        "BURN LP",
        "WITHDRAWLIQUIDITY",
        "CLOSEP",
    )

    return any(marker in stitched for marker in needle_markers)


def estimate_instruction_complexity(logs_lines: Any) -> Decimal:
    """Map log chatter into [0,1] complexity heuristic."""

    if not isinstance(logs_lines, list):
        return Decimal("0.05")

    line_count_decimal = Decimal(len(logs_lines))

    ratio = line_count_decimal / Decimal("42")

    return Decimal.min(Decimal("1"), Decimal.max(Decimal("0"), ratio))


def estimate_priority_proxy(logs_lines: Any) -> Decimal:
    """Derive searcher priority heuristic from verbosity."""

    if not isinstance(logs_lines, list):

        return Decimal("120")

    return Decimal(max(len(logs_lines), 5)) * Decimal("135")


def guess_bias_from_logs(logs_lines: Any) -> PendingMempoolEvent.__annotations__["direction_bias"]:
    """Coarse BUY/SELL classification from SPL log keywords."""

    if not isinstance(logs_lines, list):

        return "neutral"

    stitched_upper: str = " ".join(str(line).upper() for line in logs_lines)

    if "SWAP BASE IN" in stitched_upper:

        return "buy"

    if "SWAP BASE OUT" in stitched_upper:

        return "sell"

    return "neutral"


def estimate_sol_notional() -> Decimal:
    """USD placeholder avoiding heavy RPC fetch inside hot loops."""

    return Decimal("47500")


async def consume_sol_socket(
    *,
    stop_evt: asyncio.Event,
    websocket: Any,
    coordinator: SurveillanceCoordinator,
    server_subscription_to_pool: dict[int, str],
) -> None:
    """Read notifications until upstream closes."""

    buffers: PoolRingBuffers = coordinator.buffers

    while not stop_evt.is_set():

        try:

            inbound = await asyncio.wait_for(websocket.recv(), timeout=150.0)

        except asyncio.CancelledError:

            raise

        except asyncio.TimeoutError:

            logger.debug("Solana websocket idle tick")

            continue

        textual = inbound.decode("utf-8") if isinstance(inbound, bytes) else str(inbound)

        try:

            decoded_message: dict[str, Any] = msgspec.json.decode(textual.encode())

        except msgspec.DecodeError:

            logger.debug("Sol notification parse dropped")

            continue

        await hydrate_logs_notification(
            decoded=decoded_message,
            server_subscription_to_pool=server_subscription_to_pool,
            coordinator=coordinator,
            buffers=buffers,
        )


async def solana_watch_loop(
    *,
    stop_evt: asyncio.Event,
    coordinator: SurveillanceCoordinator,
    solana_wss_url: str,
) -> None:
    """
    Manage resilient Solana observability sockets.

    Section 25.5 Architecture — Isolate WebSocket ingestion from MCP tools.
    """

    endpoint = solana_wss_url.strip()

    if endpoint == "":

        logger.warning("SOLANA_WSS missing; SOL surveillance degraded")

        await coordinator.note_sol_health(degraded=True)

        await stop_evt.wait()

        return

    backoff: float = 2.75

    server_subscription_to_pool: dict[int, str] = {}

    while not stop_evt.is_set():

        try:

            async with websocket_connect(endpoint, ping_interval=20.0, max_size=None) as socket:

                await coordinator.note_sol_health(degraded=False)

                backoff = 2.75

                await bootstrap_sol_watchers(
                    websocket=socket,
                    coordinator=coordinator,
                    server_subscription_to_pool=server_subscription_to_pool,
                )

                await consume_sol_socket(
                    stop_evt=stop_evt,
                    websocket=socket,
                    coordinator=coordinator,
                    server_subscription_to_pool=server_subscription_to_pool,
                )

        except asyncio.CancelledError:

            logger.info("Solana surveillance loop cancelled upstream")

            raise

        except Exception as exc:

            logger.warning("Solana websocket session reset | backoff={} | err={}", backoff, exc)

            await coordinator.note_sol_health(degraded=True)

            capped_sleep = float(min(backoff, 42.0))

            await asyncio.sleep(capped_sleep)

            backoff *= float(1.6)

    logger.info("Solana surveillance shut down cleanly")
