"""
Sequential Thinking MCP Server for POLARIS ATLAS layer.
Gives R1 a scratchpad for step-by-step confluence reasoning.
"""

import asyncio
import msgspec
from typing import Any
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("polaris-sequential-thinking")
_sessions: dict[str, list[dict]] = {}

def _serialize(data) -> str:
    return msgspec.json.encode(data).decode("utf-8")

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List thinking tools."""
    return [
        _get_think_tool(),
        _get_finish_tool(),
        _get_trace_tool(),
    ]

def _get_think_tool() -> Tool:
    return Tool(
        name="think",
        description="Add a thought to your scratchpad.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "thought": {"type": "string"},
                "step_type": {"type": "string", "enum": ["gate", "scoring", "anomaly", "revision"]},
                "score_contribution": {"type": "number"}
            },
            "required": ["session_id", "thought", "step_type"]
        }
    )

def _get_finish_tool() -> Tool:
    return Tool(
        name="finish_thinking",
        description="Finalise thinking and return the trace.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "final_score": {"type": "integer", "minimum": 0, "maximum": 220},
                "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                "trade_decision": {"type": "string", "enum": ["LONG", "SHORT", "SKIP"]}
            },
            "required": ["session_id", "final_score", "confidence", "trade_decision"]
        }
    )

def _get_trace_tool() -> Tool:
    return Tool(
        name="get_thinking_trace",
        description="Retrieve the full thought chain.",
        inputSchema={
            "type": "object",
            "properties": {"session_id": {"type": "string"}},
            "required": ["session_id"]
        }
    )

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route thinking tools."""
    try:
        handlers = {
            "think": _handle_think,
            "finish_thinking": _handle_finish,
            "get_thinking_trace": _handle_trace,
        }
        if name not in handlers:
            raise ValueError(f"Unknown tool: {name}")
        res = await handlers[name](arguments)
        return [TextContent(type="text", text=_serialize(res))]
    except Exception as e:
        return [TextContent(type="text", text=_serialize({"error": str(e)}))]

async def _handle_think(args: dict) -> dict:
    sid = args["session_id"]
    sess = _sessions.setdefault(sid, [])
    if len(sess) >= 12:
        return {"status": "max_reached", "message": "Call finish_thinking now."}
    step = {
        "step": len(sess) + 1,
        "type": args["step_type"],
        "thought": args["thought"],
        "contrib": args.get("score_contribution", 0)
    }
    sess.append(step)
    return {"status": "recorded", "step": step["step"], "running": sum(s["contrib"] for s in sess)}

async def _handle_finish(args: dict) -> dict:
    sid = args["session_id"]
    thoughts = _sessions.pop(sid, [])
    calc = sum(t["contrib"] for t in thoughts)
    return {
        "session_id": sid,
        "thoughts": thoughts,
        "calculated": calc,
        "declared": args["final_score"],
        "decision": args["trade_decision"]
    }

async def _handle_trace(args: dict) -> dict:
    sid = args["session_id"]
    thoughts = _sessions.get(sid, [])
    return {"session_id": sid, "thoughts": thoughts}

if __name__ == "__main__":
    async def main():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
    asyncio.run(main())
