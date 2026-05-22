"""
Dual-frequency background synchronisation for TradFi and treasury Web3 data.

MacroCrossMarketAgent - Fiat Gravity Engine: FRED series refresh every 12 hours,
USDT/USDC mint-burn scan every ~10 minutes into timestamped signed flows.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal

import httpx
from loguru import logger
from web3 import AsyncWeb3

from .clients.fred_api import fetch_all_configured_series
from .clients.treasury_web3 import collect_mint_burn_events
from .config import (
    FRED_SERIES_IDS,
    LOOKBACK_BLOCKS_30D,
    TRADFI_POLL_INTERVAL_SECONDS,
    USDC_CONTRACT_ADDRESS,
    USDT_CONTRACT_ADDRESS,
    WEB3_POLL_INTERVAL_SECONDS,
)
from .models import StablecoinFlows


_tradfi_lock: asyncio.Lock = asyncio.Lock()
_stablecoin_lock: asyncio.Lock = asyncio.Lock()

_tradfi_observations: dict[str, list[tuple[date, Decimal]]] = {}
_usdt_stamped: list[tuple[datetime, Decimal]] = []
_usdc_stamped: list[tuple[datetime, Decimal]] = []
_tradfi_task: asyncio.Task[None] | None = None
_web3_task: asyncio.Task[None] | None = None

_block_ts_cache: dict[int, datetime] = {}


def _sum_window(
    stamped: list[tuple[datetime, Decimal]],
    now_utc: datetime,
    hours: int,
) -> Decimal:
    """Sum signed flows with timestamp >= now - hours."""
    cutoff: datetime = now_utc - timedelta(hours=hours)
    total: Decimal = Decimal("0")
    for ts, amt in stamped:
        if ts >= cutoff:
            total += amt
    return total


def build_stablecoin_flows_snapshot(
    usdt_stamped: list[tuple[datetime, Decimal]],
    usdc_stamped: list[tuple[datetime, Decimal]],
    now_utc: datetime,
    populated: bool,
) -> StablecoinFlows:
    """Derive rolling-window StablecoinFlows from cached stamped events."""
    u24: Decimal = _sum_window(usdt_stamped, now_utc, 24)
    u7: Decimal = _sum_window(usdt_stamped, now_utc, 24 * 7)
    u30: Decimal = _sum_window(usdt_stamped, now_utc, 24 * 30)
    c24: Decimal = _sum_window(usdc_stamped, now_utc, 24)
    c7: Decimal = _sum_window(usdc_stamped, now_utc, 24 * 7)
    c30: Decimal = _sum_window(usdc_stamped, now_utc, 24 * 30)
    status: Literal["OK", "DEGRADED"] = "OK" if populated else "DEGRADED"
    return StablecoinFlows(
        usdt_net_usd_24h=u24,
        usdt_net_usd_7d=u7,
        usdt_net_usd_30d=u30,
        usdc_net_usd_24h=c24,
        usdc_net_usd_7d=c7,
        usdc_net_usd_30d=c30,
        combined_net_usd_24h=u24 + c24,
        combined_net_usd_7d=u7 + c7,
        combined_net_usd_30d=u30 + c30,
        status=status,
    )


async def get_tradfi_observations() -> dict[str, list[tuple[date, Decimal]]]:
    """Return a shallow copy of cached FRED observations."""
    async with _tradfi_lock:
        return {k: list(v) for k, v in _tradfi_observations.items()}


async def get_stablecoin_stamped_pair() -> tuple[
    list[tuple[datetime, Decimal]],
    list[tuple[datetime, Decimal]],
]:
    """Return cached USDT and USDC stamped flows."""
    async with _stablecoin_lock:
        return list(_usdt_stamped), list(_usdc_stamped)


async def get_stablecoin_flows_view() -> StablecoinFlows:
    """Expose StablecoinFlows from current caches for MCP tools."""
    now_utc: datetime = datetime.now(tz=timezone.utc)
    async with _stablecoin_lock:
        usdt_local: list[tuple[datetime, Decimal]] = list(_usdt_stamped)
        usdc_local: list[tuple[datetime, Decimal]] = list(_usdc_stamped)
    populated: bool = len(usdt_local) > 0 or len(usdc_local) > 0
    return build_stablecoin_flows_snapshot(
        usdt_local,
        usdc_local,
        now_utc,
        populated,
    )


async def _tradfi_loop(http_client: httpx.AsyncClient, api_key: str) -> None:
    """Poll FRED observations on a 12-hour cadence."""
    while True:
        try:
            batch: dict[str, list[tuple[date, Decimal]]] = (
                await fetch_all_configured_series(
                    http_client,
                    api_key,
                    FRED_SERIES_IDS,
                )
            )
            async with _tradfi_lock:
                _tradfi_observations.clear()
                _tradfi_observations.update(batch)
            logger.info(
                "TradFi cache refreshed | MacroCrossMarketAgent - Fiat Gravity Engine | "
                "series_count={}",
                len(batch),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("TradFi polling failed | error={}", exc)
        await asyncio.sleep(float(TRADFI_POLL_INTERVAL_SECONDS))


async def _web3_poll_cycle(w3: AsyncWeb3) -> None:
    """Scan USDT/USDC mint-burn across the trailing 30-day block window."""
    head: int = int(await w3.eth.block_number)
    from_block: int = max(0, head - LOOKBACK_BLOCKS_30D)
    usdt_events: list[tuple[datetime, Decimal]] = await collect_mint_burn_events(
        w3,
        USDT_CONTRACT_ADDRESS,
        from_block,
        head,
        _block_ts_cache,
    )
    usdc_events: list[tuple[datetime, Decimal]] = await collect_mint_burn_events(
        w3,
        USDC_CONTRACT_ADDRESS,
        from_block,
        head,
        _block_ts_cache,
    )
    async with _stablecoin_lock:
        _usdt_stamped.clear()
        _usdt_stamped.extend(usdt_events)
        _usdc_stamped.clear()
        _usdc_stamped.extend(usdc_events)
    logger.debug(
        "Stablecoin treasury scan complete | head={} | usdt_evt={} | usdc_evt={}",
        head,
        len(usdt_events),
        len(usdc_events),
    )


async def _web3_loop(w3: AsyncWeb3) -> None:
    """Poll Ethereum mint-burn logs on a 10-minute cadence."""
    while True:
        try:
            await _web3_poll_cycle(w3)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Web3 polling failed | error={}", exc)
        await asyncio.sleep(float(WEB3_POLL_INTERVAL_SECONDS))


async def start_background_sync(
    w3: AsyncWeb3,
    api_key: str,
    http_client: httpx.AsyncClient,
) -> None:
    """Launch TradFi and Web3 asyncio loops (idempotent)."""
    global _tradfi_task, _web3_task  # noqa: PLW0603
    if _tradfi_task is None or _tradfi_task.done():
        _tradfi_task = asyncio.create_task(
            _tradfi_loop(http_client, api_key),
            name="predict_macro_tradfi",
        )
    if _web3_task is None or _web3_task.done():
        _web3_task = asyncio.create_task(
            _web3_loop(w3),
            name="predict_macro_web3",
        )
    logger.info(
        "MacroCrossMarketAgent - Fiat Gravity Engine background sync started"
    )


async def stop_background_sync() -> None:
    """Cancel background tasks."""
    global _tradfi_task, _web3_task  # noqa: PLW0603
    for task in (_tradfi_task, _web3_task):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    _tradfi_task = None
    _web3_task = None
    logger.info(
        "MacroCrossMarketAgent - Fiat Gravity Engine background sync stopped"
    )
