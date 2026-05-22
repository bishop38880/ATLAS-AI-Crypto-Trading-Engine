"""
Ethereum Transfer log scanning for USDT / USDC treasury mint and burn events.

MacroCrossMarketAgent - Fiat Gravity Engine normalises raw uint256 amounts with
exactly six decimals on Ethereum USDT/USDC (divide by 10**6 using Decimal).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from web3 import AsyncWeb3
from web3.types import FilterParams, LogReceipt

from ..config import (
    BLOCK_CHUNK_SIZE,
    ETH_GETLOGS_TIMEOUT_SECONDS,
    STABLECOIN_DECIMALS_ETHEREUM,
    TRANSFER_EVENT_TOPIC,
    ZERO_ADDRESS_TOPIC,
)


def _topic_hex(topic: Any) -> str:
    """Normalise a topic field to a lowercase 0x-prefixed hex string."""
    if hasattr(topic, "hex"):
        hex_attr: Any = topic.hex()
        raw: str = hex_attr if isinstance(hex_attr, str) else "0x" + bytes(topic).hex()
    else:
        raw = "0x" + bytes(topic).hex()
    raw_lower: str = raw.lower()
    if raw_lower.startswith("0x"):
        return raw_lower
    return "0x" + raw_lower


def _normalise_uint256_token_amount(raw_value: int) -> Decimal:
    """Convert raw ERC-20 amount to Decimal USD units (6 decimals)."""
    scale: Decimal = Decimal(10) ** STABLECOIN_DECIMALS_ETHEREUM
    return Decimal(raw_value) / scale


async def _fetch_block_timestamp(
    w3: AsyncWeb3,
    block_number: int,
    cache: dict[int, datetime],
) -> datetime | None:
    """Resolve block timestamp with an in-memory LRU-style cache."""
    cached: datetime | None = cache.get(block_number)
    if cached is not None:
        return cached

    try:
        block: Any = await asyncio.wait_for(
            w3.eth.get_block(block_number),
            timeout=15.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "get_block failed | block={} | error={}",
            block_number,
            exc,
        )
        return None

    ts_raw: Any = block.get("timestamp")
    if ts_raw is None:
        return None
    ts_int: int = int(ts_raw)
    resolved: datetime = datetime.fromtimestamp(ts_int, tz=timezone.utc)
    cache[block_number] = resolved
    return resolved


async def _resolve_timestamps_for_blocks(
    w3: AsyncWeb3,
    block_numbers: set[int],
    cache: dict[int, datetime],
) -> dict[int, datetime]:
    """Batch-resolve unique block timestamps."""
    resolved: dict[int, datetime] = {}
    semaphore: asyncio.Semaphore = asyncio.Semaphore(12)

    async def _one(block_num: int) -> None:
        async with semaphore:
            ts: datetime | None = await _fetch_block_timestamp(w3, block_num, cache)
            if ts is not None:
                resolved[block_num] = ts

    await asyncio.gather(*[_one(bn) for bn in sorted(block_numbers)])
    return resolved


async def _get_logs_chunk(
    w3: AsyncWeb3,
    token_address: str,
    from_block: int,
    to_block: int,
) -> list[LogReceipt] | None:
    """Invoke eth_getLogs for one inclusive block range."""
    checksum: str = AsyncWeb3.to_checksum_address(token_address)
    try:
        return await asyncio.wait_for(
            w3.eth.get_logs(
                FilterParams(
                    fromBlock=from_block,
                    toBlock=to_block,
                    address=checksum,
                    topics=[TRANSFER_EVENT_TOPIC],
                )
            ),
            timeout=ETH_GETLOGS_TIMEOUT_SECONDS,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "eth_getLogs timeout | token={} | blocks={}-{}",
            token_address,
            from_block,
            to_block,
        )
        return None
    except Exception as exc:
        logger.error(
            "eth_getLogs failed | token={} | blocks={}-{} | error={}",
            token_address,
            from_block,
            to_block,
            exc,
        )
        return None


def _parse_signed_transfer(
    log_entry: LogReceipt,
) -> tuple[int, Decimal] | None:
    """
    Map a Transfer log to (block_number, signed_usd_amount).

    Mint: from == zero. Burn: to == zero. Ambiguous rows are skipped.
    """
    topics: Any = log_entry.get("topics", [])
    if not isinstance(topics, list) or len(topics) < 3:
        return None

    from_topic: str = _topic_hex(topics[1])
    to_topic: str = _topic_hex(topics[2])

    data_field: Any = log_entry.get("data", b"")
    raw_bytes: bytes = bytes(data_field) if not isinstance(data_field, bytes) else data_field
    raw_value: int = int.from_bytes(raw_bytes, byteorder="big", signed=False)
    amount: Decimal = _normalise_uint256_token_amount(raw_value)

    block_any: Any = log_entry.get("blockNumber")
    if block_any is None:
        return None
    block_number: int = int(block_any)

    is_mint: bool = from_topic == ZERO_ADDRESS_TOPIC
    is_burn: bool = to_topic == ZERO_ADDRESS_TOPIC
    if is_mint and not is_burn:
        return block_number, amount
    if is_burn and not is_mint:
        return block_number, -amount
    return None


async def collect_mint_burn_events(
    w3: AsyncWeb3,
    token_address: str,
    from_block: int,
    to_block: int,
    block_ts_cache: dict[int, datetime],
) -> list[tuple[datetime, Decimal]]:
    """
    Scan mint/burn Transfer events across chunked ranges.

    Returns timestamped signed USD flows (positive=mint, negative=burn).
    """
    events: list[tuple[int, Decimal]] = []
    cursor: int = from_block
    while cursor <= to_block:
        chunk_end: int = min(cursor + BLOCK_CHUNK_SIZE - 1, to_block)
        logs: list[LogReceipt] | None = await _get_logs_chunk(
            w3,
            token_address,
            cursor,
            chunk_end,
        )
        cursor = chunk_end + 1
        if logs is None:
            continue
        for log_entry in logs:
            parsed: tuple[int, Decimal] | None = _parse_signed_transfer(log_entry)
            if parsed is None:
                continue
            events.append(parsed)

    blocks_needed: set[int] = {block_num for block_num, _ in events}
    ts_map: dict[int, datetime] = await _resolve_timestamps_for_blocks(
        w3,
        blocks_needed,
        block_ts_cache,
    )

    stamped: list[tuple[datetime, Decimal]] = []
    for block_number, signed_amt in events:
        ts: datetime | None = ts_map.get(block_number)
        if ts is None:
            continue
        stamped.append((ts, signed_amt))
    return stamped
