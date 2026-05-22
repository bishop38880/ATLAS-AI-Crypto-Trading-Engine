"""
Background event poller for RWA mint/burn flow tracking.

Section 5 Architecture: Macro Context — Institutional Rotation.
Launches an ``asyncio`` background task that periodically scans ERC-20
Transfer events for all tracked RWA contracts. Maintains a thread-safe,
in-memory cache of 24h/7d net flows and PoR collateralisation ratios.
The FastMCP tools query this cache instantly — zero blocking.

The poller respects RPC rate limits by:
1. Chunking ``eth_getLogs`` into ``BLOCK_CHUNK_SIZE`` blocks.
2. Sleeping ``POLL_INTERVAL_SECONDS`` between full scan cycles.
3. Using a single ``asyncio.Lock`` for cache mutation safety.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger
from web3 import AsyncWeb3

from .config import (
    LOOKBACK_BLOCKS_24H,
    LOOKBACK_BLOCKS_7D,
    POLL_INTERVAL_SECONDS,
    RWA_CONTRACTS,
    SUPPORTED_SYMBOLS,
    ZERO_ADDRESS,
)
from .web3_engine import (
    fetch_latest_round_data,
    fetch_total_supply,
    fetch_transfer_logs_chunked,
)


# ---------------------------------------------------------------------------
# In-memory state cache — guarded by _cache_lock
# ---------------------------------------------------------------------------
_cache: dict[str, dict[str, Any]] = {}
_cache_lock: asyncio.Lock = asyncio.Lock()
_poller_task: asyncio.Task[None] | None = None


async def get_cached_flows(symbol: str) -> dict[str, Any] | None:
    """
    Return cached flow data for a symbol, or None if not yet populated.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    async with _cache_lock:
        entry: dict[str, Any] | None = _cache.get(symbol)
        if entry is not None:
            return dict(entry)  # shallow copy for safety
        return None


async def get_all_cached() -> dict[str, dict[str, Any]]:
    """
    Return a snapshot of the entire cache.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    async with _cache_lock:
        return {k: dict(v) for k, v in _cache.items()}


async def start_poller(w3: AsyncWeb3) -> None:
    """
    Launch the background poller task.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Called once at MCP server startup. Safe to call multiple times — idempotent.
    """
    global _poller_task  # noqa: PLW0603
    if _poller_task is not None and not _poller_task.done():
        logger.info("Poller already running — skipping duplicate start")
        return

    _poller_task = asyncio.create_task(
        _poll_loop(w3), name="por_rwa_poller"
    )
    logger.info("PoR/RWA background poller started")


async def stop_poller() -> None:
    """Cancel the background poller gracefully."""
    global _poller_task  # noqa: PLW0603
    if _poller_task is not None and not _poller_task.done():
        _poller_task.cancel()
        try:
            await _poller_task
        except asyncio.CancelledError:
            pass  # expected on graceful shutdown
    _poller_task = None
    logger.info("PoR/RWA background poller stopped")


async def _poll_loop(w3: AsyncWeb3) -> None:
    """
    Continuous polling loop — scans all RWA contracts each cycle.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    logger.info(
        "Poller entering main loop | interval={}s | symbols={}",
        POLL_INTERVAL_SECONDS, list(SUPPORTED_SYMBOLS),
    )
    while True:
        try:
            await _execute_poll_cycle(w3)
        except asyncio.CancelledError:
            logger.info("Poller loop cancelled — exiting")
            raise
        except Exception as exc:
            logger.exception(
                "Poller cycle failed — will retry | error={}", exc
            )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _execute_poll_cycle(w3: AsyncWeb3) -> None:
    """
    Execute one full poll cycle across all tracked RWA contracts.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    current_block: int = await w3.eth.block_number
    logger.debug("Poll cycle start | block={}", current_block)

    for symbol, contract_info in RWA_CONTRACTS.items():
        try:
            await _poll_single_token(w3, symbol, contract_info, current_block)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Poll failed for token | symbol={} | error={}", symbol, exc
            )
            await _update_cache_degraded(symbol)


async def _poll_single_token(
    w3: AsyncWeb3,
    symbol: str,
    contract_info: dict[str, str | int],
    current_block: int,
) -> None:
    """
    Poll a single RWA token: supply, PoR oracle, and Transfer events.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    token_address: str = str(contract_info["token_address"])
    token_decimals: int = int(contract_info["token_decimals"])
    oracle_address: str = str(contract_info["por_oracle_address"])
    oracle_decimals: int = int(contract_info["oracle_decimals"])

    total_supply: Decimal = await fetch_total_supply(
        w3, token_address, token_decimals
    )

    por_data: dict[str, Any] = await _fetch_por_data(
        w3, oracle_address, oracle_decimals, total_supply
    )

    flow_24h: tuple[Decimal, Decimal] = await _fetch_flow_window(
        w3, token_address, token_decimals, current_block, LOOKBACK_BLOCKS_24H
    )
    flow_7d: tuple[Decimal, Decimal] = await _fetch_flow_window(
        w3, token_address, token_decimals, current_block, LOOKBACK_BLOCKS_7D
    )

    await _store_poll_results(
        symbol, total_supply, por_data, flow_24h, flow_7d
    )


async def _fetch_por_data(
    w3: AsyncWeb3,
    oracle_address: str,
    oracle_decimals: int,
    total_supply: Decimal,
) -> dict[str, Any]:
    """
    Fetch PoR oracle data and compute collateralisation ratio.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Returns a dict with reserve, ratio, timestamps, and health flags.
    """
    if oracle_address == ZERO_ADDRESS:
        return _empty_por_data()

    try:
        reserve, updated_at = await fetch_latest_round_data(
            w3, oracle_address, oracle_decimals
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error("PoR oracle fetch failed | error={}", exc)
        return _empty_por_data()

    ratio: Decimal = _calculate_ratio(reserve, total_supply)
    stale: bool = _is_oracle_stale(updated_at)

    return {
        "off_chain_reserve": reserve,
        "collateralization_ratio": ratio,
        "is_fully_backed": ratio >= Decimal("1.0"),
        "oracle_updated_at": updated_at,
        "oracle_stale": stale,
        "status": "OK",
    }


def _empty_por_data() -> dict[str, Any]:
    """Return a safe empty PoR result when oracle is unavailable."""
    return {
        "off_chain_reserve": Decimal("0"),
        "collateralization_ratio": Decimal("0"),
        "is_fully_backed": False,
        "oracle_updated_at": datetime.now(tz=timezone.utc),
        "oracle_stale": True,
        "status": "NO_ORACLE",
    }


def _calculate_ratio(reserve: Decimal, supply: Decimal) -> Decimal:
    """Compute collateralisation ratio with zero-division safety."""
    if supply <= Decimal("0"):
        return Decimal("0")
    return reserve / supply


def _is_oracle_stale(updated_at: datetime) -> bool:
    """Return True if oracle data is older than 24 hours."""
    age_seconds: float = (
        datetime.now(tz=timezone.utc) - updated_at
    ).total_seconds()
    return age_seconds > 86_400


async def _fetch_flow_window(
    w3: AsyncWeb3,
    token_address: str,
    token_decimals: int,
    current_block: int,
    lookback_blocks: int,
) -> tuple[Decimal, Decimal]:
    """Fetch mints and burns for a given block lookback window."""
    from_block: int = max(0, current_block - lookback_blocks)
    return await fetch_transfer_logs_chunked(
        w3, token_address, from_block, current_block, token_decimals
    )


async def _store_poll_results(
    symbol: str,
    total_supply: Decimal,
    por_data: dict[str, Any],
    flow_24h: tuple[Decimal, Decimal],
    flow_7d: tuple[Decimal, Decimal],
) -> None:
    """Write poll results into the in-memory cache under lock."""
    net_24h: Decimal = flow_24h[0] - flow_24h[1]
    net_7d: Decimal = flow_7d[0] - flow_7d[1]
    avg_daily_7d: Decimal = net_7d / Decimal("7")

    entry: dict[str, Any] = {
        "symbol": symbol,
        "total_supply": total_supply,
        "mint_volume_24h": flow_24h[0],
        "burn_volume_24h": flow_24h[1],
        "net_flow_24h": net_24h,
        "mint_volume_7d": flow_7d[0],
        "burn_volume_7d": flow_7d[1],
        "net_flow_7d": net_7d,
        "avg_daily_net_flow_7d": avg_daily_7d,
        **por_data,
        "last_updated": datetime.now(tz=timezone.utc),
    }

    async with _cache_lock:
        _cache[symbol] = entry

    logger.info(
        "Cache updated | symbol={} | supply={} | net_24h={} | ratio={}",
        symbol, total_supply, net_24h, por_data.get("collateralization_ratio"),
    )


async def _update_cache_degraded(symbol: str) -> None:
    """Mark a symbol as DEGRADED in the cache without overwriting good data."""
    async with _cache_lock:
        if symbol in _cache:
            _cache[symbol]["status"] = "DEGRADED"
        else:
            _cache[symbol] = {
                "symbol": symbol,
                "status": "DEGRADED",
                "last_updated": datetime.now(tz=timezone.utc),
            }
