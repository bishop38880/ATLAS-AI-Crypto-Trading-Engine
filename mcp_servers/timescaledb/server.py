"""
TimescaleDB MCP Server for POLARIS ATLAS layer.
Provides read-only analytical query tools over historical market data.
"""

import asyncio
import msgspec
from datetime import datetime
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent
from backend.config.asset_universe import POLARIS_DASHBOARD_VALID_BASES

from .db import fetch, fetchval

server = Server("polaris-timescaledb")

VALID_SYMBOLS = POLARIS_DASHBOARD_VALID_BASES

def _validate_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if s not in VALID_SYMBOLS:
        raise ValueError(f"Symbol '{s}' not in universe.")
    return s

def _serialize(data) -> str:
    return msgspec.json.encode(data).decode("utf-8")

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List analytical database tools."""
    return [
        _get_funding_tool(),
        _get_oi_tool(),
        _get_price_context_tool(),
        _get_flow_tool(),
        _get_liq_tool(),
    ]

def _get_funding_tool() -> Tool:
    return Tool(
        name="get_funding_rate_stats",
        description="Get funding rate statistics over a lookback period.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_days": {"type": "integer", "default": 30}
            },
            "required": ["symbol"]
        }
    )

def _get_oi_tool() -> Tool:
    return Tool(
        name="get_oi_trend",
        description="Get open interest trend analysis.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_hours": {"type": "integer", "default": 48}
            },
            "required": ["symbol"]
        }
    )

def _get_price_context_tool() -> Tool:
    return Tool(
        name="get_price_context",
        description="Get price context: MAs, ATR, high/low.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "interval": {"type": "string", "enum": ["1h", "4h", "1d"], "default": "4h"}
            },
            "required": ["symbol"]
        }
    )

def _get_flow_tool() -> Tool:
    return Tool(
        name="get_exchange_flow_trend",
        description="Get on-chain exchange flow trend.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_days": {"type": "integer", "default": 7}
            },
            "required": ["symbol"]
        }
    )

def _get_liq_tool() -> Tool:
    return Tool(
        name="get_liquidation_context",
        description="Get recent liquidation data.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_hours": {"type": "integer", "default": 24}
            },
            "required": ["symbol"]
        }
    )

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls."""
    try:
        symbol = _validate_symbol(arguments.get("symbol", ""))
        handlers = {
            "get_funding_rate_stats": _handle_funding,
            "get_oi_trend": _handle_oi,
            "get_price_context": _handle_price,
            "get_exchange_flow_trend": _handle_flow,
            "get_liquidation_context": _handle_liq,
        }
        if name not in handlers:
            raise ValueError(f"Unknown tool: {name}")
        res = await handlers[name](symbol, arguments)
        return [TextContent(type="text", text=_serialize(res))]
    except Exception as e:
        return [TextContent(type="text", text=_serialize({"error": str(e)}))]

async def _handle_funding(symbol: str, args: dict) -> dict:
    lb = min(args.get("lookback_days", 30), 180)
    rows = await fetch("""
        SELECT ROUND(AVG(funding_rate)::numeric, 6) as mean,
               ROUND(STDDEV(funding_rate)::numeric, 6) as std,
               ROUND(MAX(funding_rate)::numeric, 6) as max
        FROM derivatives_data WHERE symbol = $1 AND time > NOW() - INTERVAL '1 day' * $2
    """, symbol, lb)
    cur = await fetchval("SELECT funding_rate FROM derivatives_data WHERE symbol = $1 ORDER BY time DESC LIMIT 1", symbol)
    res = rows[0] if rows else {}
    res.update({"current": float(cur) if cur else None, "symbol": symbol})
    return res

async def _handle_oi(symbol: str, args: dict) -> dict:
    lb = min(args.get("lookback_hours", 48), 720)
    latest = await fetch("SELECT open_interest FROM derivatives_data WHERE symbol = $1 ORDER BY time DESC LIMIT 1", symbol)
    ago_24h = await fetch("""
        SELECT open_interest FROM derivatives_data 
        WHERE symbol = $1 AND time <= NOW() - INTERVAL '24 hours' ORDER BY time DESC LIMIT 1
    """, symbol)
    cur_oi = float(latest[0]["open_interest"]) if latest else None
    res = {"symbol": symbol, "current_oi": cur_oi}
    if cur_oi and ago_24h:
        prev = float(ago_24h[0]["open_interest"])
        res["change_24h_pct"] = round(100 * (cur_oi - prev) / prev, 2) if prev else 0
    return res

async def _handle_price(symbol: str, args: dict) -> dict:
    iv = args.get("interval", "4h")
    cur = await fetchval("SELECT close FROM market_data WHERE symbol = $1 ORDER BY time DESC LIMIT 1", symbol)
    res = {"symbol": symbol, "price": float(cur) if cur else None}
    return res

async def _handle_flow(symbol: str, args: dict) -> dict:
    lb = min(args.get("lookback_days", 7), 30)
    rows = await fetch("""
        SELECT ROUND(SUM(exchange_inflow - exchange_outflow)::numeric, 2) as netflow
        FROM onchain_data WHERE symbol = $1 AND time > NOW() - INTERVAL '1 day' * $2
    """, symbol, lb)
    return rows[0] if rows else {}

async def _handle_liq(symbol: str, args: dict) -> dict:
    lb = min(args.get("lookback_hours", 24), 168)
    rows = await fetch("""
        SELECT ROUND(SUM(liquidations_long)::numeric, 0) as longs,
               ROUND(SUM(liquidations_short)::numeric, 0) as shorts
        FROM derivatives_data WHERE symbol = $1 AND time > NOW() - INTERVAL '1 hour' * $2
    """, symbol, lb)
    return rows[0] if rows else {}

if __name__ == "__main__":
    async def main():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
    asyncio.run(main())
