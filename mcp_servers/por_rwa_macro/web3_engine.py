"""
Async Web3 engine for on-chain RWA data retrieval.

Section 5 Architecture: Macro Context — Institutional Rotation.
Handles ``eth_getLogs`` chunking for Transfer event scanning,
``latestRoundData()`` for Chainlink PoR oracles, and ``totalSupply()``
for ERC-20 tokens. All uint256 values are normalised to ``Decimal``
using on-chain ``decimals()`` to prevent floating-point precision loss.

Uses ``web3[async]`` with ``AsyncHTTPProvider`` for non-blocking I/O.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from web3 import AsyncWeb3
from web3.contract import AsyncContract
from web3.types import FilterParams, LogReceipt

from .config import (
    AGGREGATOR_V3_ABI,
    BLOCK_CHUNK_SIZE,
    ERC20_ABI,
    TRANSFER_EVENT_TOPIC,
    ZERO_ADDRESS,
    ZERO_ADDRESS_PADDED,
)


def _normalise_uint256(raw_value: int, decimals: int) -> Decimal:
    """
    Convert a raw uint256 to a human-readable Decimal.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Uses integer-only Decimal arithmetic to avoid any float intermediary.
    """
    divisor: Decimal = Decimal(10) ** decimals
    return Decimal(raw_value) / divisor


def _build_erc20_contract(
    w3: AsyncWeb3, address: str
) -> AsyncContract:
    """Instantiate a minimal ERC-20 contract handle."""
    checksum_address: str = AsyncWeb3.to_checksum_address(address)
    return w3.eth.contract(address=checksum_address, abi=ERC20_ABI)


def _build_aggregator_contract(
    w3: AsyncWeb3, address: str
) -> AsyncContract:
    """Instantiate a minimal Chainlink AggregatorV3 contract handle."""
    checksum_address: str = AsyncWeb3.to_checksum_address(address)
    return w3.eth.contract(
        address=checksum_address, abi=AGGREGATOR_V3_ABI
    )


async def fetch_total_supply(
    w3: AsyncWeb3,
    token_address: str,
    token_decimals: int,
) -> Decimal:
    """
    Fetch current totalSupply of an ERC-20 token, normalised to Decimal.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    contract: AsyncContract = _build_erc20_contract(w3, token_address)
    raw_supply: int = await contract.functions.totalSupply().call()
    return _normalise_uint256(raw_supply, token_decimals)


async def fetch_on_chain_decimals(
    w3: AsyncWeb3,
    token_address: str,
) -> int:
    """Read the ``decimals()`` from an ERC-20 contract."""
    contract: AsyncContract = _build_erc20_contract(w3, token_address)
    result: int = await contract.functions.decimals().call()
    return int(result)


async def fetch_latest_round_data(
    w3: AsyncWeb3,
    oracle_address: str,
    oracle_decimals: int,
) -> tuple[Decimal, datetime]:
    """
    Fetch the latest Chainlink PoR oracle answer and its update timestamp.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Returns (normalised_answer, updated_at_utc).
    """
    contract: AsyncContract = _build_aggregator_contract(w3, oracle_address)
    result: tuple[Any, ...] = await contract.functions.latestRoundData().call()
    raw_answer: int = result[1]
    updated_at_epoch: int = result[3]

    normalised_answer: Decimal = _normalise_uint256(raw_answer, oracle_decimals)
    updated_at: datetime = datetime.fromtimestamp(
        updated_at_epoch, tz=timezone.utc
    )
    return normalised_answer, updated_at


async def fetch_oracle_decimals(
    w3: AsyncWeb3,
    oracle_address: str,
) -> int:
    """Read the ``decimals()`` from a Chainlink AggregatorV3."""
    contract: AsyncContract = _build_aggregator_contract(w3, oracle_address)
    result: int = await contract.functions.decimals().call()
    return int(result)


async def fetch_transfer_logs_chunked(
    w3: AsyncWeb3,
    token_address: str,
    from_block: int,
    to_block: int,
    token_decimals: int,
) -> tuple[Decimal, Decimal]:
    """
    Scan Transfer events in chunks to find mints and burns.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Mints: ``from == 0x0`` (zero-address sends tokens into existence).
    Burns: ``to == 0x0`` (tokens are destroyed).

    Returns (total_minted, total_burned) both normalised via ``Decimal``.
    """
    total_minted: Decimal = Decimal("0")
    total_burned: Decimal = Decimal("0")
    checksum_address: str = AsyncWeb3.to_checksum_address(token_address)

    current_block: int = from_block
    while current_block <= to_block:
        chunk_end: int = min(current_block + BLOCK_CHUNK_SIZE - 1, to_block)
        minted, burned = await _fetch_chunk_flows(
            w3, checksum_address, current_block, chunk_end, token_decimals
        )
        total_minted += minted
        total_burned += burned
        current_block = chunk_end + 1

    return total_minted, total_burned


async def _fetch_chunk_flows(
    w3: AsyncWeb3,
    checksum_address: str,
    start: int,
    end: int,
    token_decimals: int,
) -> tuple[Decimal, Decimal]:
    """
    Fetch mint/burn flows for a single block chunk.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    logs: list[LogReceipt] | None = await _get_logs_safe(
        w3, checksum_address, start, end
    )
    if logs is None:
        return Decimal("0"), Decimal("0")
    return _parse_mint_burn_logs(logs, token_decimals)


async def _get_logs_safe(
    w3: AsyncWeb3,
    checksum_address: str,
    start: int,
    end: int,
) -> list[LogReceipt] | None:
    """
    Call ``eth_getLogs`` with timeout and error handling.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Returns None on failure so the caller degrades gracefully.
    """
    try:
        return await asyncio.wait_for(
            w3.eth.get_logs(
                FilterParams(
                    fromBlock=start,
                    toBlock=end,
                    address=checksum_address,
                    topics=[TRANSFER_EVENT_TOPIC],
                )
            ),
            timeout=15.0,
        )
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        logger.warning(
            "eth_getLogs timeout | address={} | blocks={}–{}",
            checksum_address, start, end,
        )
        return None
    except Exception as exc:
        logger.error(
            "eth_getLogs failed | address={} | blocks={}–{} | error={}",
            checksum_address, start, end, exc,
        )
        return None



def _parse_mint_burn_logs(
    logs: list[LogReceipt],
    token_decimals: int,
) -> tuple[Decimal, Decimal]:
    """
    Parse Transfer logs to separate mints from burns.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Mint: topic[1] (from) == zero address.
    Burn: topic[2] (to) == zero address.
    """
    minted: Decimal = Decimal("0")
    burned: Decimal = Decimal("0")

    for log_entry in logs:
        topics: list[bytes] = log_entry.get("topics", [])
        if len(topics) < 3:
            continue

        raw_value: int = int.from_bytes(log_entry.get("data", b""), "big")
        normalised: Decimal = _normalise_uint256(raw_value, token_decimals)

        from_topic: str = "0x" + topics[1].hex()
        to_topic: str = "0x" + topics[2].hex()

        if from_topic == ZERO_ADDRESS_PADDED:
            minted += normalised
        elif to_topic == ZERO_ADDRESS_PADDED:
            burned += normalised

    return minted, burned
