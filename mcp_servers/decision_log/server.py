"""
Decision Log MCP Server for POLARIS.

Exposes read-only query tools over the decision_log TimescaleDB table.
Allows DeepSeek R1 to retrieve historical trade outcomes during reasoning,
closing the feedback loop between decisions and results.

Transport: stdio
"""

import asyncio
import msgspec
from datetime import datetime
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent
from .db import fetch

server = Server("polaris-decision-log")
MAX_ROWS = 50


def _serialize(rows: list[dict]) -> str:
    """JSON-serialize rows, converting datetime objects to ISO strings."""
    return msgspec.json.encode(rows).decode("utf-8")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available decision log tools."""
    return [
        _get_historical_outcomes_tool(),
        _get_win_rate_tool(),
        _get_best_regimes_tool(),
        _get_similar_conditions_tool(),
        _get_component_correlation_tool(),
    ]


def _get_historical_outcomes_tool() -> Tool:
    return Tool(
        name="get_historical_outcomes",
        description="Query historical trading decisions and outcomes.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "action": {"type": "string", "enum": ["LONG", "SHORT", "SKIP"]},
                "min_score": {"type": "integer", "minimum": 0, "maximum": 220},
                "max_score": {"type": "integer", "minimum": 0, "maximum": 220},
                "lookback_hours": {"type": "integer", "default": 720},
                "limit": {"type": "integer", "default": 20, "maximum": 50}
            }
        }
    )


def _get_win_rate_tool() -> Tool:
    return Tool(
        name="get_win_rate_by_score_range",
        description="Get win rate statistics grouped by score ranges.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "lookback_days": {"type": "integer", "default": 30}
            }
        }
    )


def _get_best_regimes_tool() -> Tool:
    return Tool(
        name="get_best_performing_regimes",
        description="Identify regimes with the highest win rates.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_trades": {"type": "integer", "default": 10},
                "lookback_days": {"type": "integer", "default": 30}
            }
        }
    )


def _get_similar_conditions_tool() -> Tool:
    return Tool(
        name="get_similar_conditions",
        description="Find past decisions made under similar conditions.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "derivatives_score": {"type": "integer"},
                "whale_signal": {"type": "string", "enum": ["accumulation", "distribution"]},
                "regime": {"type": "string"}
            },
            "required": ["symbol"]
        }
    )


def _get_component_correlation_tool() -> Tool:
    return Tool(
        name="get_component_correlation",
        description="Analyse which components correlate with outcomes.",
        inputSchema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "outcome_horizon": {"type": "string", "enum": ["1h", "4h", "24h"], "default": "4h"}
            }
        }
    )


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route tool calls to handlers."""
    handlers = {
        "get_historical_outcomes": _handle_historical_outcomes,
        "get_win_rate_by_score_range": _handle_win_rate,
        "get_best_performing_regimes": _handle_best_regimes,
        "get_similar_conditions": _handle_similar_conditions,
        "get_component_correlation": _handle_component_correlation,
    }
    if name not in handlers:
        return [TextContent(type="text", text=f"Error: Unknown tool {name}")]
    try:
        rows = await handlers[name](arguments)
        return [TextContent(type="text", text=_serialize(rows))]
    except Exception as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def _handle_historical_outcomes(args: dict) -> list:
    conds, params, idx = ["outcome_4h_pct IS NOT NULL"], [], 1
    if args.get("symbol"):
        conds.append(f"symbol = ${idx}"); params.append(args["symbol"].upper()); idx += 1
    if args.get("action"):
        conds.append(f"action = ${idx}"); params.append(args["action"]); idx += 1
    if args.get("min_score") is not None:
        conds.append(f"score >= ${idx}"); params.append(args["min_score"]); idx += 1
    if args.get("max_score") is not None:
        conds.append(f"score <= ${idx}"); params.append(args["max_score"]); idx += 1
    
    lookback = args.get("lookback_hours", 720)
    conds.append(f"timestamp > NOW() - INTERVAL '{lookback} hours'")
    limit = min(args.get("limit", 20), MAX_ROWS)
    query = f"SELECT * FROM decision_log WHERE {' AND '.join(conds)} ORDER BY timestamp DESC LIMIT {limit}"
    return await fetch(query, *params)


async def _handle_win_rate(args: dict) -> list:
    symbol_filter = ""
    params = []
    if args.get("symbol"):
        symbol_filter = "AND symbol = $1"
        params.append(args["symbol"].upper())
    lookback = args.get("lookback_days", 30)
    query = f"""
        SELECT 
            CASE WHEN score < 130 THEN 'L' WHEN score < 170 THEN 'M' ELSE 'H' END as range,
            COUNT(*) as count, AVG(CASE WHEN outcome_4h_pct > 0 THEN 1 ELSE 0 END) as win_rate
        FROM decision_log WHERE outcome_4h_pct IS NOT NULL {symbol_filter}
        AND timestamp > NOW() - INTERVAL '{lookback} days' GROUP BY 1
    """
    return await fetch(query, *params)


async def _handle_best_regimes(args: dict) -> list:
    lookback, min_t = args.get("lookback_days", 30), args.get("min_trades", 10)
    query = f"""
        SELECT context_regime, COUNT(*), AVG(CASE WHEN outcome_4h_pct > 0 THEN 1 ELSE 0 END) as win_rate
        FROM decision_log WHERE outcome_4h_pct IS NOT NULL 
        AND timestamp > NOW() - INTERVAL '{lookback} days'
        GROUP BY 1 HAVING COUNT(*) >= {min_t} ORDER BY win_rate DESC
    """
    return await fetch(query)


async def _handle_similar_conditions(args: dict) -> list:
    symbol, deriv = args["symbol"].upper(), args.get("derivatives_score", 40)
    whale, regime = args.get("whale_signal", "neutral"), args.get("regime", "ranging")
    query = """
        SELECT timestamp, symbol, score, action, outcome_4h_pct FROM decision_log
        WHERE symbol = $1 AND derivatives_score BETWEEN $2 - 10 AND $2 + 10
        AND (whale_signal = $3 OR context_regime = $4) ORDER BY timestamp DESC LIMIT 10
    """
    return await fetch(query, symbol, deriv, whale, regime)


async def _handle_component_correlation(args: dict) -> list:
    sym_filter, params = "", []
    if args.get("symbol"):
        sym_filter = "AND symbol = $1"; params.append(args["symbol"].upper())
    h = args.get("outcome_horizon", "4h")
    query = f"SELECT 'derivatives', CORR(derivatives_score, outcome_{h}_pct) FROM decision_log WHERE outcome_{h}_pct IS NOT NULL {sym_filter}"
    return await fetch(query, *params)


if __name__ == "__main__":
    mcp.server.stdio.run_server(server)
