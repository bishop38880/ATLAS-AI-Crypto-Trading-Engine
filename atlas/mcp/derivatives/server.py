"""Derivatives Context MCP Server (ATLAS/HYDRA Integration).

Exposes real-time Perpetual Futures intelligence from Redis to the ATLAS agentic layer.
Strictly read-replica interface with zero direct exchange connections.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import msgspec
import redis.asyncio as redis
from loguru import logger
from mcp.server.fastmcp import FastMCP

from atlas.shared.config import PolarisSettings
from atlas.shared.hydra_asset import hydra_base_asset
from atlas.mcp.derivatives.models import (
    UnifiedPerpState,
    LiquidationCluster,
    SqueezeRiskReport,
)


# ─── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Initialise and cleanup Redis connection pool."""
    global _redis_pool  # noqa: PLW0603
    settings = PolarisSettings()

    hydra_url = settings.resolved_hydra_redis_url()
    logger.info("Initialising DerivativesContext Redis pool | url={}", hydra_url)
    _redis_pool = redis.from_url(
        hydra_url,
        decode_responses=False,  # msgspec handles raw bytes
        max_connections=10,
    )
    
    try:
        yield {}
    finally:
        if _redis_pool:
            await _redis_pool.aclose()
            _redis_pool = None
        logger.info("DerivativesContext Redis pool closed")


# ─── FastMCP Instance ───────────────────────────────────────────────────────

mcp = FastMCP(
    "DerivativesContext",
    instructions=(
        "Exposes real-time Perpetual Futures intelligence (Funding, Open Interest, "
        "Liquidation Clusters, and Cascade Alerts) to the ATLAS agentic layer. "
        "Strictly read-only Redis interface."
    ),
    lifespan=_lifespan,
)

_redis_pool: redis.Redis | None = None


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _get_redis() -> redis.Redis:
    """Helper to get the global Redis pool."""
    if _redis_pool is None:
        msg = "Redis pool not initialised. Is the server running?"
        raise RuntimeError(msg)
    return _redis_pool


# ─── Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_perp_context(symbol: str) -> UnifiedPerpState:
    """Fetch current Open Interest and Funding APY for a symbol.

    Args:
        symbol: The trading symbol (e.g., "BTC-PERP").
    """
    db = await _get_redis()
    key = f"coinalyze:state:{symbol}"
    raw = await db.get(key)
    
    if not raw:
        msg = f"No state found for {symbol} at {key}"
        raise ValueError(msg)
        
    data = msgspec.json.decode(raw)
    return UnifiedPerpState(**data)


@mcp.tool()
async def get_liquidation_clusters(symbol: str) -> list[LiquidationCluster]:
    """Fetch the most dense liquidation clusters closest to current price.

    Args:
        symbol: The trading symbol (e.g., "BTC-PERP").
    """
    db = await _get_redis()
    key = f"hydra:clusters:{hydra_base_asset(symbol)}"
    raw = await db.get(key)
    
    if not raw:
        return []
        
    # All Redis payloads must be decoded using msgspec.json.decode
    data = msgspec.json.decode(raw)
    return [LiquidationCluster(**d) for d in data]


@mcp.tool()
async def evaluate_squeeze_risk(symbol: str, intended_direction: str) -> SqueezeRiskReport:
    """Evaluate if an active liquidation cascade poses a risk to the trade.

    Args:
        symbol: The trading symbol (e.g., "BTC-PERP").
        intended_direction: Either "LONG" or "SHORT".
    """
    db = await _get_redis()
    direction = intended_direction.upper()
    if direction not in {"LONG", "SHORT"}:
        msg = "intended_direction must be 'LONG' or 'SHORT'"
        raise ValueError(msg)

    # Check live cascade state from Redis key
    key = f"hydra:cascades:live:{hydra_base_asset(symbol)}"
    raw = await db.get(key)
    
    active_cascade = False
    momentum_veto = False
    rationale = "No active cascades detected."
    hazard_level = "LOW"

    if raw:
        cascade_data = msgspec.json.decode(raw)
        active_cascade = cascade_data.get("active", False)
        cascade_side = cascade_data.get("side", "").upper()  # "LONG" or "SHORT"
        
        if active_cascade:
            # Absolute Veto Logic: 
            # If intending to LONG, but a long-liquidation flush is active, return momentum_veto = True.
            # (A long cascade means price is being forced down by liquidations).
            if direction == cascade_side:
                momentum_veto = True
                hazard_level = "CRITICAL"
                rationale = (
                    f"Active {cascade_side} liquidation cascade detected. "
                    f"Counter-trend entry blocked by HYDRA momentum veto."
                )
            else:
                hazard_level = "MEDIUM"
                rationale = (
                    f"Active {cascade_side} cascade in progress. "
                    f"Pro-trend momentum high, but exercise caution."
                )

    return SqueezeRiskReport(
        hazard_level=hazard_level,
        momentum_veto=momentum_veto,
        hydra_alert_active=active_cascade,
        rationale=rationale,
    )


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
