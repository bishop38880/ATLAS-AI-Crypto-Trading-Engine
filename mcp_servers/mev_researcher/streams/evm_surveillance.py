"""Ethereum pending-transaction ingestion loop."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import msgspec.json
import websockets
from loguru import logger
from web3 import AsyncWeb3

from ..cache import PoolRingBuffers
from ..coordinator import SurveillanceCoordinator, ethereum_price_quote
from ..models import PendingMempoolEvent

BUY_SELECTOR_PREFIXES: tuple[str, ...] = (
    "7ff36ab5",
    "04e45aaf",
)

SELL_SELECTOR_PREFIXES: tuple[str, ...] = (
    "18cbfaf5",
    "8803dbee",
)

DRAIN_SELECTOR_PREFIXES: tuple[str, ...] = (
    "b6f9de95",
    "baa2abde",
    "02751cec",
)


def utc_millis_now() -> int:
    """Current epoch milliseconds."""

    millis: float = time.time() * 1000.0
    return int(millis)


def _guess_bias(calldata_hex: str) -> PendingMempoolEvent.__annotations__["direction_bias"]:
    """Infer directional bias via entrypoint selector."""

    if len(calldata_hex) < 10:
        return "neutral"

    head: str = calldata_hex.lower()[2:10]

    if head in BUY_SELECTOR_PREFIXES:
        return "buy"

    if head in SELL_SELECTOR_PREFIXES:
        return "sell"

    return "neutral"


def _looks_like_drain(calldata_hex: str) -> bool:
    """Heuristic liquidity removal detection."""

    if len(calldata_hex) < 10:
        return False

    signature: str = calldata_hex.lower()[2:10]

    return any(signature.startswith(prefix[:8]) or signature == prefix for prefix in DRAIN_SELECTOR_PREFIXES)


def _mentions_pool(calldata_hex: str, pool_lower: str) -> bool:
    """Detect pool address substring inside ABI blob."""

    clean_pool: str = pool_lower.lower().replace("0x", "").strip()

    normalized_input: str = calldata_hex.lower().replace("0x", "")

    return clean_pool != "" and clean_pool in normalized_input


def _scaled_complexity(calldata_hex: str) -> Decimal:
    """Map ABI length into [0,1] complexity heuristic."""

    base_len: Decimal = Decimal(len(calldata_hex or ""))

    divisor: Decimal = Decimal("6400")

    ratio: Decimal = base_len / divisor

    capped: Decimal = Decimal.min(Decimal("1"), ratio)

    return Decimal.max(Decimal("0"), capped)


async def ethereum_pending_loop(
    *,
    stop_evt: asyncio.Event,
    coordinator: SurveillanceCoordinator,
    http_rpc_url: str,
    mempool_wss_url: str,
    env_map: dict[str, str | None],
) -> None:
    """
    Consume `newPendingTransactions` and hydrate ring buffers.

    Section 25.5 Architecture — background surveillance (never inside tools).
    """
    stripped_http: str = http_rpc_url.strip()
    trimmed_wss: str = mempool_wss_url.strip()

    if not trimmed_wss.startswith("ws"):
        await coordinator.note_eth_health(degraded=True)

        logger.warning("ETH_MEMPOOL_WSS missing; ethereum surveillance degraded")

        await stop_evt.wait()

        return

    if not stripped_http:

        await coordinator.note_eth_health(degraded=True)

        logger.warning("ETH_HTTP_RPC_URL missing for tx hydrate; ethereum degraded")

        await stop_evt.wait()

        return

    semaphore: asyncio.Semaphore = asyncio.Semaphore(8)

    buffers: PoolRingBuffers = coordinator.buffers

    w3_provider: AsyncWeb3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(stripped_http))

    resolved_quote: Decimal = ethereum_price_quote(
        {k: v or "" for k, v in env_map.items()},
    )

    backoff_seconds: float = 2.5

    subscription_payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_subscribe",
        "params": ["newPendingTransactions"],
    }

    websocket: Any = None

    while not stop_evt.is_set():

        try:

            websocket = await asyncio.wait_for(
                websockets.connect(trimmed_wss, max_size=None),
                timeout=25.0,
            )

            await coordinator.note_eth_health(degraded=False)

            backoff_seconds = 2.5

            await websocket.send(
                msgspec.json.encode(subscription_payload).decode("utf-8"),
            )

            while not stop_evt.is_set():

                raw_message = await asyncio.wait_for(websocket.recv(), timeout=120.0)

                asyncio.create_task(
                    _process_pending_frame(
                        raw_message=raw_message,
                        coordinator=coordinator,
                        semaphore=semaphore,
                        buffers=buffers,
                        w3=w3_provider,
                        eth_quote=resolved_quote,
                    ),
                )

        except asyncio.CancelledError:
            logger.info("Ethereum surveillance cancelled")

            raise

        except Exception as exc:

            logger.warning("Ethereum mempool reconnect | err={}", exc)

            await coordinator.note_eth_health(degraded=True)

            await asyncio.sleep(min(backoff_seconds, 35.0))
            backoff_seconds = min(backoff_seconds * float(2.0), float(38.0))

        finally:

            if websocket:

                await websocket.close()
                websocket = None

            await asyncio.sleep(0)

    logger.info("Ethereum surveillance loop halted")


async def _process_pending_frame(
    *,
    raw_message: object,
    coordinator: SurveillanceCoordinator,
    semaphore: asyncio.Semaphore,
    buffers: PoolRingBuffers,
    w3: AsyncWeb3,
    eth_quote: Decimal,
) -> None:

    tx_hash: str | None = _extract_tx_hash(raw_message)

    if tx_hash is None:
        return

    async with semaphore:

        try:

            transaction = await asyncio.wait_for(
                w3.eth.get_transaction(tx_hash),
                timeout=15.0,
            )

        except asyncio.TimeoutError:

            logger.debug("Hydration timeout | tx={}", tx_hash)

            return

        except asyncio.CancelledError:

            raise

        except Exception as exc:

            logger.warning("Hydration failure | tx={} | err={}", tx_hash, exc)

            return

        await ingest_transaction_candidate(
            transaction=transaction,
            coordinator=coordinator,
            buffers=buffers,
            eth_quote=eth_quote,
            tx_signature=tx_hash,
        )


def _extract_tx_hash(raw_message: object) -> str | None:

    text_payload = _normalize_frame_text(raw_message)

    if text_payload == "":
        return None

    try:

        decoded: dict[str, Any] = msgspec.json.decode(text_payload.encode("utf-8"))

    except msgspec.DecodeError:

        return None

    candidate: Any = decoded.get("params", {}).get("result")

    if not isinstance(candidate, str):
        return None

    if not candidate.startswith(("0x", "0X")):
        return None

    return candidate


def _normalize_frame_text(raw_message: object) -> str:

    if isinstance(raw_message, bytes):
        raw_text = raw_message.decode("utf-8", errors="ignore")

    elif isinstance(raw_message, str):
        raw_text = raw_message

    else:

        logger.debug("Unknown websocket frame {}", type(raw_message))

        raw_text = ""

    return raw_text


async def ingest_transaction_candidate(
    *,
    transaction: dict[str, Any] | Any,
    coordinator: SurveillanceCoordinator,
    buffers: PoolRingBuffers,
    eth_quote: Decimal,
    tx_signature: str,
) -> None:
    """Translate pending tx payloads into pooled events."""

    txn_dict = _txn_as_dict(transaction)
    impacted = _matching_pools(txn_dict=txn_dict, coordinator=coordinator)

    if len(impacted) == 0:
        return

    calldata_text: str = _coerce_hex_input(txn_dict.get("input"))

    wei_int: int = _coerce_positive_int(txn_dict.get("value"))

    heuristic_notional, usd_projection = estimate_notional(
        wei_int=wei_int,
        abi_len=len(calldata_text),
        eth_quote=eth_quote,
    )

    wei_priority_raw: int = _resolve_priority(txn_dict)

    priority_micros = Decimal(str(wei_priority_raw))

    bias = _guess_bias(calldata_text)

    complexity = _scaled_complexity(calldata_text)

    removal_flag = _looks_like_drain(calldata_text)

    timestamp_ms = utc_millis_now()

    for pool_impacted in impacted:

        event_payload = PendingMempoolEvent(
            chain="ethereum",
            pool_address=pool_impacted,
            tx_signature=tx_signature,
            direction_bias=bias,
            notional_usd=max(usd_projection, heuristic_notional * eth_quote),
            priority_fee_micros=priority_micros,
            complexity_score=complexity,
            captured_at_unix_ms=timestamp_ms,
            liquidity_removal_candidate=removal_flag,
        )

        await buffers.record_event(event_payload)


async def _matching_pools(
    *,
    txn_dict: dict[str, Any],
    coordinator: SurveillanceCoordinator,
) -> set[str]:

    calldata_hex: str = _coerce_hex_input(txn_dict.get("input"))

    recipient_candidate: Any = txn_dict.get("to")

    recipient_low: str = ""

    if isinstance(recipient_candidate, str):
        recipient_low = recipient_candidate.lower()

    watchlist_eth: set[str] = await coordinator.ethereum_watch_addresses()

    affected: set[str] = set()

    if recipient_low in watchlist_eth:
        affected.add(recipient_low)

    for watcher in watchlist_eth:

        if _mentions_pool(calldata_hex, watcher):

            affected.add(watcher.lower())

    return affected


def _txn_as_dict(transaction: dict[str, Any] | Any) -> dict[str, Any]:

    if isinstance(transaction, dict):
        return dict(transaction)

    attrs = getattr(transaction, "_asdict", None)

    if callable(attrs):

        return dict(attrs())

    return dict(vars(transaction))


def _coerce_hex_input(raw_input: Any) -> str:

    if isinstance(raw_input, str):

        stripped = raw_input.strip()

        if stripped == "":
            return "0x"

        if not stripped.startswith("0x"):
            return "0x" + stripped

        return stripped

    return "0x"


def _coerce_positive_int(raw_value: Any) -> int:

    if raw_value is None:
        return 0

    if isinstance(raw_value, int):

        return max(raw_value, 0)

    if isinstance(raw_value, str):

        try:

            if raw_value.startswith(("0x", "0X")):

                parsed = int(raw_value, base=16)

            else:

                parsed = int(raw_value)

            return max(parsed, 0)

        except ValueError:

            return 0

    return max(int(raw_value), 0)


def _resolve_priority(txn_dict: dict[str, Any]) -> int:

    max_priority_candidate: Any = txn_dict.get("maxPriorityFeePerGas")

    max_fee_candidate: Any = txn_dict.get("maxFeePerGas")

    gas_price_candidate: Any = txn_dict.get("gasPrice")

    prioritized = max(
        [
            _coerce_positive_int(max_priority_candidate),
            _coerce_positive_int(max_fee_candidate),
            _coerce_positive_int(gas_price_candidate),
        ],
    )

    return prioritized


def estimate_notional(
    *,
    wei_int: int,
    abi_len: int,
    eth_quote: Decimal,
) -> tuple[Decimal, Decimal]:
    """Return ETH-notional heuristic and crude USD surrogate."""

    eth_component: Decimal = Decimal(wei_int) / Decimal(10 ** 18)

    abi_fraction: Decimal = Decimal(abi_len) / Decimal("50000")

    heuristic_notional: Decimal = Decimal.max(eth_component, abi_fraction)

    usd_projection: Decimal = heuristic_notional * eth_quote + abi_fraction * Decimal("95000")

    return heuristic_notional, usd_projection
