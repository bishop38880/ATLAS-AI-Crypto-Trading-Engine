"""Token Unlocks MCP Server — FastMCP entry point.

ATLAS Intelligence Layer — NewsMacroAgent Integration.

Exposes three rigorously typed tools to the ATLAS agent layer:

1. ``get_supply_ratios``       — Layer 1 (DefiLlama) tokenomics.
2. ``check_impending_unlocks`` — Layer 2 (Dune) on-chain vesting.
3. ``evaluate_exit_liquidity_risk`` — Master aggregation + penalty.

**Exit Liquidity Guardrail:**
If an impending cliff unlock exceeds a configured threshold of the
circulating supply within 72 hours, the ``conviction_suppression``
flag is emitted to veto long positions and prevent the system from
being used as exit liquidity by VC unlocks.

Transport: stdio (designed for MCP subprocess invocation).
Secrets: ``DUNE_API_KEY`` read from ``.env`` via ``dotenv``.
"""

from __future__ import annotations

import asyncio
import os
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from mcp.server import Server
from mcp.types import TextContent, Tool

import msgspec

from mcp_servers.token_unlocks.clients.defillama import DefiLlamaClient
from mcp_servers.token_unlocks.clients.dune import DuneClient
from mcp_servers.token_unlocks.models import (
    ENCODER,
    DefiLlamaSupply,
    DuneUnlockSchedule,
    UnlockRiskReport,
)
from mcp_servers.token_unlocks.risk_engine import evaluate_exit_liquidity

# ─── Bootstrap ───────────────────────────────────────────────────────────────

load_dotenv()

# NOTE: Documented Sentinel exception — MCP servers run as isolated
# subprocesses outside the ATLAS PolarisSettings scope.  Like the
# kill switch's minimal config loader, this server reads its own .env
# directly via dotenv + os.getenv.  This is intentional and explicit.
_DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
_DEFAULT_THRESHOLD = os.getenv("UNLOCK_THRESHOLD_PERCENT", "2.0")
_MAX_POLL_ATTEMPTS = int(os.getenv("DUNE_MAX_POLL_ATTEMPTS", "20"))
_POLL_TIMEOUT_S = float(os.getenv("DUNE_POLL_TIMEOUT_SECONDS", "120"))

if not _DUNE_API_KEY:
    logger.warning(
        "DUNE_API_KEY not set — Layer 2 (Dune) tools will return DEGRADED",
    )

# ─── Singleton clients ──────────────────────────────────────────────────────

_defillama = DefiLlamaClient()
_dune = DuneClient(
    api_key=_DUNE_API_KEY,
    max_poll_attempts=_MAX_POLL_ATTEMPTS,
    poll_timeout_seconds=_POLL_TIMEOUT_S,
)

server = Server("polaris-token-unlocks")


def _serialize(obj: object) -> str:
    """Encode any ``msgspec.Struct`` to a JSON string via the
    custom Decimal-aware encoder."""
    return ENCODER.encode(obj).decode("utf-8")


# ─── Tool Definitions ───────────────────────────────────────────────────────

@server.list_tools()
async def list_tools() -> list[Tool]:
    """Expose the three Token Unlocks tools."""
    return [
        _supply_ratios_tool(),
        _impending_unlocks_tool(),
        _exit_liquidity_risk_tool(),
    ]


def _supply_ratios_tool() -> Tool:
    """Tool definition for ``get_supply_ratios``."""
    return Tool(
        name="get_supply_ratios",
        description=(
            "ATLAS Intelligence Layer — NewsMacroAgent. "
            "Layer 1 (DefiLlama): Fetch circulating vs. total supply "
            "ratios for an asset. Returns Decimal-precise tokenomics."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Uppercase ticker (e.g. SOL, ARB).",
                },
            },
            "required": ["symbol"],
        },
    )


def _impending_unlocks_tool() -> Tool:
    """Tool definition for ``check_impending_unlocks``."""
    return Tool(
        name="check_impending_unlocks",
        description=(
            "ATLAS Intelligence Layer — NewsMacroAgent. "
            "Layer 2 (Dune Analytics): Execute a Dune query to fetch "
            "on-chain vesting unlock schedule. Returns normalised "
            "events with Decimal amounts and exact datetimes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Uppercase ticker.",
                },
                "dune_query_id": {
                    "type": "integer",
                    "description": "Dune query ID for the vesting contract.",
                },
            },
            "required": ["symbol", "dune_query_id"],
        },
    )


def _exit_liquidity_risk_tool() -> Tool:
    """Tool definition for ``evaluate_exit_liquidity_risk``."""
    return Tool(
        name="evaluate_exit_liquidity_risk",
        description=(
            "ATLAS Intelligence Layer — NewsMacroAgent. "
            "Exit Liquidity Guardrail: Master aggregation tool. "
            "Calls both Layer 1 (DefiLlama) and Layer 2 (Dune), then "
            "evaluates whether conviction_suppression should apply. "
            "If unlock > threshold% of circulating supply within 72h, "
            "returns conviction_suppression=True to veto long positions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Uppercase ticker.",
                },
                "dune_query_id": {
                    "type": "integer",
                    "description": "Dune query ID for the vesting contract.",
                },
                "threshold_percent": {
                    "type": "string",
                    "description": (
                        "Suppression threshold as a Decimal string "
                        "(default '2.0' = 2% of circulating supply)."
                    ),
                    "default": "2.0",
                },
            },
            "required": ["symbol", "dune_query_id"],
        },
    )


# ─── Tool Dispatch ───────────────────────────────────────────────────────────

@server.call_tool()
async def call_tool(
    name: str, arguments: dict[str, Any],
) -> list[TextContent]:
    """Route tool calls to handlers."""
    handlers = {
        "get_supply_ratios": _handle_supply_ratios,
        "check_impending_unlocks": _handle_impending_unlocks,
        "evaluate_exit_liquidity_risk": _handle_exit_liquidity_risk,
    }
    if name not in handlers:
        error_payload = {"error": f"Unknown tool: {name}"}
        return [TextContent(
            type="text",
            text=msgspec.json.encode(error_payload).decode("utf-8"),
        )]
    try:
        result = await handlers[name](arguments)
        return [TextContent(type="text", text=result)]
    except asyncio.CancelledError:
        raise  # ALWAYS re-raise — Sentinel invariant
    except Exception as exc:
        logger.exception("Tool dispatch error | tool={}", name)
        error_payload = {"error": str(exc), "tool": name}
        return [TextContent(
            type="text",
            text=msgspec.json.encode(error_payload).decode("utf-8"),
        )]


# ─── Handler Implementations ────────────────────────────────────────────────

async def _handle_supply_ratios(
    args: dict[str, Any],
) -> str:
    """Handle ``get_supply_ratios`` — Layer 1 DefiLlama fetch."""
    symbol = str(args.get("symbol", "")).upper()
    supply = await _defillama.fetch_supply(symbol)
    return _serialize(supply)


async def _handle_impending_unlocks(
    args: dict[str, Any],
) -> str:
    """Handle ``check_impending_unlocks`` — Layer 2 Dune fetch."""
    symbol = str(args.get("symbol", "")).upper()
    query_id = int(args.get("dune_query_id", 0))
    schedule = await _dune.fetch_unlocks(symbol, query_id)
    return _serialize(schedule)


async def _handle_exit_liquidity_risk(
    args: dict[str, Any],
) -> str:
    """Handle ``evaluate_exit_liquidity_risk`` — master aggregation.

    Calls both Layer 1 and Layer 2 in parallel via ``asyncio.gather``,
    then runs the risk engine to produce the ``UnlockRiskReport``.
    """
    symbol = str(args.get("symbol", "")).upper()
    query_id = int(args.get("dune_query_id", 0))
    threshold = Decimal(
        str(args.get("threshold_percent", _DEFAULT_THRESHOLD)),
    )

    supply, schedule = await asyncio.gather(
        _defillama.fetch_supply(symbol),
        _dune.fetch_unlocks(symbol, query_id),
        return_exceptions=False,
    )

    report = evaluate_exit_liquidity(supply, schedule, threshold)
    return _serialize(report)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import mcp.server.stdio

    async def main() -> None:
        async with mcp.server.stdio.stdio_server() as streams:
            read_stream, write_stream = streams
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(main())
