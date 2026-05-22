"""
Mempool Surveillance & MEV Researcher MCP (FastMCP).

Section 25.5 Architecture: Protective Swarms and MEV-Aware Execution.
Background workers fill a sliding-window cache; tools read the cache only
(no WebSocket or long-lived stream inside a tool handler).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx
import msgspec
from loguru import logger
from mcp.server.fastmcp import FastMCP

from .cache_layer import ThreatCacheCoordinator, canonical_evm_address, canonical_sol_address
from .config import load_mev_config
from .mev_engine import (
    build_toxicity_report,
    derive_execution_strategy,
    scan_pending_for_rugs,
)
from .models import (
    ExecutionRecommendation,
    RugPullScanReport,
    ToxicityReport,
    WatchlistAck,
)
from .streams.evm_surveillance import run_evm_pending_loop, run_flashbots_sse_loop
from .streams.sol_surveillance import run_sol_surveillance

_config_path = Path(__file__).resolve().parent / ".env"

_UNSUPPORTED_CHAIN_JSON = (
    '{"error":"unsupported_chain","detail":"chain must be ethereum or solana"}'
)

_coordinator: ThreatCacheCoordinator | None = None


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Start surveillance workers; cancel cleanly on shutdown."""
    global _coordinator

    cfg = load_mev_config(_config_path)
    _coordinator = ThreatCacheCoordinator()
    shutdown = asyncio.Event()
    tasks: list[asyncio.Task[None]] = []
    sse_client: httpx.AsyncClient | None = None

    if cfg.surveillance_enabled and cfg.eth_http_url and cfg.eth_ws_url:
        tasks.append(
            asyncio.create_task(
                run_evm_pending_loop(
                    cfg.eth_http_url,
                    cfg.eth_ws_url,
                    _coordinator,
                    shutdown,
                    eth_usd_hint=cfg.eth_usd_hint,
                    fetch_concurrency=cfg.evm_fetch_concurrency,
                ),
            ),
        )
    elif cfg.surveillance_enabled:
        logger.warning(
            "EVM MEV surveillance idle | need ETH_HTTP_URL and ETH_WS_URL",
        )

    if cfg.surveillance_enabled and cfg.flashbots_sse_url.strip():
        sse_client = httpx.AsyncClient(
            http2=True,
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=300.0),
        )
        tasks.append(
            asyncio.create_task(
                run_flashbots_sse_loop(
                    sse_client,
                    cfg.flashbots_sse_url,
                    _coordinator,
                    shutdown,
                    eth_usd_hint=cfg.eth_usd_hint,
                ),
            ),
        )

    if cfg.surveillance_enabled and cfg.sol_ws_url and cfg.sol_http_url:
        tasks.append(
            asyncio.create_task(
                run_sol_surveillance(
                    _coordinator,
                    cfg,
                    shutdown,
                ),
            ),
        )

    try:
        yield {}
    finally:
        shutdown.set()
        for handle in tasks:
            handle.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if sse_client is not None:
            await sse_client.aclose()
        _coordinator = None


mcp = FastMCP(
    "mev-researcher",
    lifespan=_lifespan,
    instructions=(
        "Section 25.5 Architecture: MEV-Aware Execution. Mempool Toxicity Index "
        "(MTI) and routing guidance (VWAP / ICEBERG / PROTECTIVE_SWARM / ABORT). "
        "Register pool addresses with manage_surveillance_watchlist before expecting data."
    ),
)


def _active_coordinator() -> ThreatCacheCoordinator:
    if _coordinator is None:
        raise RuntimeError("MEV coordinator not initialised")
    return _coordinator


def _serialize(obj: Any) -> str:
    if hasattr(obj, "model_dump"):
        return msgspec.json.encode(obj.model_dump(mode="json")).decode("utf-8")
    return msgspec.json.encode(obj).decode("utf-8")


def _normalize_chain(chain: str) -> str:
    return chain.strip().lower()


def _chain_or_error(chain: str) -> str | None:
    ck = _normalize_chain(chain)
    if ck not in ("ethereum", "solana"):
        logger.warning("mev_tool_invalid_chain | chain={}", chain)
        return None
    return ck


@mcp.tool()
async def manage_surveillance_watchlist(
    chain: str,
    target_address: str,
    action: str,
) -> str:
    """
    Add (watch) or remove (unwatch) a DEX pool or program anchor from the
    live mempool filter. Section 25.5 — targeted watchlist only.
    """
    coord = _active_coordinator()
    ok, detail = await coord.manage_watchlist(chain, target_address, action)
    return _serialize(WatchlistAck(ok=ok, detail=detail))


@mcp.tool()
async def get_pool_toxicity_report(chain: str, pool_address: str) -> str:
    """
    Read the sliding-window cache for MTI, proxy pending volume, and threat tags.
    """
    coord = _active_coordinator()
    ck = _chain_or_error(chain)
    if ck is None:
        return _UNSUPPORTED_CHAIN_JSON
    pool_norm = (
        canonical_evm_address(pool_address) if ck == "ethereum" else canonical_sol_address(pool_address)
    )
    events = await coord.snapshot_for_pool(ck, pool_norm)
    report: ToxicityReport = build_toxicity_report(
        chain=ck,  # type: ignore[arg-type]
        pool_address=pool_norm,
        events=events,
    )
    return _serialize(report)


@mcp.tool()
async def evaluate_execution_strategy(
    chain: str,
    pool_address: str,
    size_usd: str,
    side: str,
) -> str:
    """
    Map MTI and trade intent to VWAP, ICEBERG, PROTECTIVE_SWARM, or ABORT.
    ``size_usd`` is a decimal string (e.g. '125000.50').
    """
    coord = _active_coordinator()
    ck = _chain_or_error(chain)
    if ck is None:
        return _UNSUPPORTED_CHAIN_JSON
    pool_norm = (
        canonical_evm_address(pool_address) if ck == "ethereum" else canonical_sol_address(pool_address)
    )
    events = await coord.snapshot_for_pool(ck, pool_norm)
    snapshot = build_toxicity_report(
        chain=ck,  # type: ignore[arg-type]
        pool_address=pool_norm,
        events=events,
    )
    try:
        notional = Decimal(size_usd.strip())
    except (InvalidOperation, AttributeError):
        notional = Decimal("0")

    choice: ExecutionRecommendation = derive_execution_strategy(
        mti_score=snapshot.mti_score,
        event_count=snapshot.window_event_count,
        size_trigger_usd=notional,
        side_literal=side,
    )
    return _serialize(choice)


@mcp.tool()
async def scan_for_rugs(chain: str, pool_address: str) -> str:
    """Scan buffered pending calldata for liquidity-removal / burn clustering."""
    coord = _active_coordinator()
    ck = _chain_or_error(chain)
    if ck is None:
        return _UNSUPPORTED_CHAIN_JSON
    pool_norm = (
        canonical_evm_address(pool_address) if ck == "ethereum" else canonical_sol_address(pool_address)
    )
    events = await coord.snapshot_for_pool(ck, pool_norm)
    report: RugPullScanReport = scan_pending_for_rugs(
        chain=ck,  # type: ignore[arg-type]
        pool_address=pool_norm,
        events=events,
    )
    return _serialize(report)


if __name__ == "__main__":
    mcp.run(transport="stdio")
