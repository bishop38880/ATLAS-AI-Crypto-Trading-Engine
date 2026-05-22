"""Deribit Options Flow & Volatility Surface MCP Server.

Section 26.3 Architecture — Options Skew Detector.
Foundational data provider for DerivativesContext MCP and Tier-1
DerivativesAgent.

Architecture: State Bridge pattern.
  1. On startup, launches an async background task that connects
     to Deribit via WebSocket.
  2. The WS task writes continuously into DeribitStateCache.
  3. MCP tools instantly query the cache — zero-latency, non-blocking.

Transport: stdio (consistent with fetch, timescaledb, decision_log MCPs).

Sentinel Invariants:
  - msgspec for all JSON serialization
  - Loguru positional format logging
  - No stdlib json, no pandas, no os.getenv
  - asyncio.CancelledError always re-raised
  - Pydantic v2 frozen models for all outputs
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import msgspec
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent
from loguru import logger

from .state_manager import DeribitStateCache
from .deribit_ws import DeribitWebSocketClient
from .quant_engine import (
    calculate_options_skew,
    calculate_iv_surface,
    calculate_put_call_ratio,
    calculate_block_trades,
)

# Valid coins for options queries
_VALID_COINS = {"BTC", "ETH"}

# Global state — initialised at module level, populated at startup
_cache = DeribitStateCache()
_ws_client = DeribitWebSocketClient(_cache)
_ws_task: asyncio.Task | None = None  # type: ignore[type-arg]

server = Server("polaris-deribit-options")


def _serialize(data: object) -> str:
    """Serialize an object to JSON string using msgspec.

    Handles Pydantic models by converting to dict first.

    Args:
        data: Object to serialize.

    Returns:
        JSON string.
    """
    if hasattr(data, "model_dump"):
        dump_fn = getattr(data, "model_dump")
        return msgspec.json.encode(dump_fn()).decode("utf-8")
    return msgspec.json.encode(data).decode("utf-8")


def _validate_coin(coin: str) -> str:
    """Validate and normalise coin symbol.

    Args:
        coin: Raw coin input string.

    Returns:
        Uppercased coin symbol.

    Raises:
        ValueError: If coin is not in the valid set.
    """
    c = coin.upper().strip()
    if c not in _VALID_COINS:
        msg = f"Unsupported coin '{c}'. Valid: {', '.join(sorted(_VALID_COINS))}"
        raise ValueError(msg)
    return c


# ------------------------------------------------------------------
# Tool definitions
# ------------------------------------------------------------------

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Deribit options tools.

    Section 26.3: Four tools for the DerivativesAgent.
    """
    return [
        _skew_tool(),
        _iv_surface_tool(),
        _pc_ratio_tool(),
        _block_trades_tool(),
    ]


def _skew_tool() -> Tool:
    """Build the options skew tool definition."""
    return Tool(
        name="get_options_skew",
        description=(
            "Section 26.3: Returns the current 25-delta risk reversal "
            "term structure (7d, 30d, 90d, 180d expiries). "
            "Positive RR = bullish conviction (call premium). "
            "Negative RR = bearish conviction (put premium)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "enum": ["BTC", "ETH"],
                    "description": "Underlying asset symbol.",
                },
            },
            "required": ["coin"],
        },
    )


def _iv_surface_tool() -> Tool:
    """Build the IV surface tool definition."""
    return Tool(
        name="get_iv_surface",
        description=(
            "Section 26.3: Returns a snapshot of the current implied "
            "volatility surface (Strike × Expiry × Mark IV) and ATM "
            "term structure with shape detection (Contango/Backwardation)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "enum": ["BTC", "ETH"],
                    "description": "Underlying asset symbol.",
                },
            },
            "required": ["coin"],
        },
    )


def _pc_ratio_tool() -> Tool:
    """Build the put/call ratio tool definition."""
    return Tool(
        name="get_put_call_ratio",
        description=(
            "Section 26.3: Returns aggregate Put/Call ratios based on "
            "24h Volume and Open Interest. P/C > 1.0 = bearish tilt."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "enum": ["BTC", "ETH"],
                    "description": "Underlying asset symbol.",
                },
            },
            "required": ["coin"],
        },
    )


def _block_trades_tool() -> Tool:
    """Build the block trades tool definition."""
    return Tool(
        name="get_recent_block_trades",
        description=(
            "Section 26.3: Returns recent institutional block trades "
            "exceeding the notional threshold. Tracks aggressive "
            "buying vs. selling, price, and Mark IV at execution."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "coin": {
                    "type": "string",
                    "enum": ["BTC", "ETH"],
                    "description": "Underlying asset symbol.",
                },
                "min_notional_usd": {
                    "type": "number",
                    "default": 500000,
                    "description": "Minimum USD notional threshold.",
                },
            },
            "required": ["coin"],
        },
    )


# ------------------------------------------------------------------
# Tool dispatch
# ------------------------------------------------------------------

@server.call_tool()
async def call_tool(
    name: str,
    arguments: dict,
) -> list[TextContent]:
    """Route tool calls to quantitative engine functions.

    All tools read from the in-memory State Cache populated by the
    background WebSocket task. Responses include ws_status and
    snapshot_ts for staleness awareness by the agent.
    """
    try:
        result = _dispatch_tool(name, arguments)
        return [TextContent(type="text", text=_serialize(result))]
    except asyncio.CancelledError:
        raise  # ALWAYS re-raise per Sentinel invariant
    except ValueError as exc:
        return [TextContent(
            type="text",
            text=_serialize({"error": str(exc)}),
        )]
    except Exception as exc:
        logger.exception("Tool call failed | tool={}", name)
        return [TextContent(
            type="text",
            text=_serialize({"error": f"Internal error: {exc}"}),
        )]


def _dispatch_tool(name: str, arguments: dict) -> object:
    """Dispatch a validated tool call to the correct handler.

    Args:
        name: Tool name string.
        arguments: Tool arguments dict.

    Returns:
        Pydantic response model.

    Raises:
        ValueError: If tool name is unknown or coin is invalid.
    """
    handlers = {
        "get_options_skew": _handle_skew,
        "get_iv_surface": _handle_iv_surface,
        "get_put_call_ratio": _handle_pc_ratio,
        "get_recent_block_trades": _handle_block_trades,
    }
    handler = handlers.get(name)
    if handler is None:
        msg = f"Unknown tool: {name}"
        raise ValueError(msg)
    return handler(arguments)


def _handle_skew(arguments: dict) -> object:
    """Handle get_options_skew tool call.

    Args:
        arguments: Tool arguments with 'coin'.

    Returns:
        OptionsSkewResponse model.
    """
    coin = _validate_coin(arguments.get("coin", ""))
    return calculate_options_skew(_cache, coin)


def _handle_iv_surface(arguments: dict) -> object:
    """Handle get_iv_surface tool call.

    Args:
        arguments: Tool arguments with 'coin'.

    Returns:
        IVSurfaceResponse model.
    """
    coin = _validate_coin(arguments.get("coin", ""))
    return calculate_iv_surface(_cache, coin)


def _handle_pc_ratio(arguments: dict) -> object:
    """Handle get_put_call_ratio tool call.

    Args:
        arguments: Tool arguments with 'coin'.

    Returns:
        PutCallRatioResponse model.
    """
    coin = _validate_coin(arguments.get("coin", ""))
    return calculate_put_call_ratio(_cache, coin)


def _handle_block_trades(arguments: dict) -> object:
    """Handle get_recent_block_trades tool call.

    Args:
        arguments: Tool arguments with 'coin' and optional 'min_notional_usd'.

    Returns:
        BlockTradesResponse model.
    """
    coin = _validate_coin(arguments.get("coin", ""))
    min_notional = Decimal(str(arguments.get("min_notional_usd", 500000)))
    return calculate_block_trades(_cache, coin, min_notional)


# ------------------------------------------------------------------
# Lifecycle: background WS task management
# ------------------------------------------------------------------

async def _start_ws_background_task() -> None:
    """Launch the Deribit WebSocket client as a background task.

    Called once at MCP server startup. The task runs indefinitely
    until the server shuts down.
    """
    global _ws_task
    _ws_task = asyncio.create_task(
        _ws_client.run_forever(),
        name="deribit-ws-stream",
    )
    logger.info("Deribit WS background task launched")


async def _main() -> None:
    """MCP server main entry point with lifecycle management.

    Launches the WS background task, then runs the MCP stdio server.
    On shutdown, cancels the WS task cleanly.
    """
    await _start_ws_background_task()
    try:
        async with mcp.server.stdio.stdio_server() as (r, w):
            await server.run(
                r, w, server.create_initialization_options(),
            )
    finally:
        await _cleanup_ws_task()


async def _cleanup_ws_task() -> None:
    """Cancel and await the WS background task on shutdown."""
    global _ws_task
    if _ws_task is not None and not _ws_task.done():
        _ws_task.cancel()
        try:
            await _ws_task
        except asyncio.CancelledError:
            pass  # Expected during shutdown
        logger.info("Deribit WS background task stopped")
    _ws_task = None


if __name__ == "__main__":
    asyncio.run(_main())
