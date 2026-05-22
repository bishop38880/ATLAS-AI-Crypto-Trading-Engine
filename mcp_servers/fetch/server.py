"""
Fetch MCP Server for POLARIS.
Provides HTTP GET tools for structured API calls.
"""

import asyncio
import msgspec
from urllib.parse import urlparse
import httpx
import mcp.server.stdio
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("polaris-fetch")

ALLOWED_DOMAINS = {
    "api.alternative.me", "hermes.pyth.network", "api.llama.fi",
    "coins.llama.fi", "api.coingecko.com", "api.coincap.io",
    "cryptopanic.com", "api.etherscan.io", "api.helius.xyz",
}

TIMEOUT = 8.0
MAX_BYTES = 50_000

def _serialize(data) -> str:
    return msgspec.json.encode(data).decode("utf-8")

@server.list_tools()
async def list_tools() -> list[Tool]:
    """List fetch tools."""
    return [
        Tool(
            name="fetch_json",
            description="Fetch JSON from allowed domains.",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}, "purpose": {"type": "string"}},
                "required": ["url", "purpose"]
            }
        ),
        Tool(
            name="fetch_text",
            description="Fetch raw text.",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}, "purpose": {"type": "string"}},
                "required": ["url", "purpose"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Route fetch tools."""
    url = arguments.get("url", "")
    domain = urlparse(url).netloc.lower().split(":")[0]
    if domain not in ALLOWED_DOMAINS:
        return [TextContent(type="text", text=_serialize({"error": "domain_not_allowed", "domain": domain}))]
    
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(url, headers={"User-Agent": "ATLAS/1.0"}, follow_redirects=True)
            content = resp.content[:MAX_BYTES]
            truncated = len(resp.content) > MAX_BYTES
            
            if name == "fetch_json":
                try:
                    data = msgspec.json.decode(content)
                    res = {"status": resp.status_code, "url": url, "truncated": truncated, "data": data}
                except:
                    res = {"status": resp.status_code, "url": url, "error": "json_decode_failed"}
            else:
                res = {"status": resp.status_code, "url": url, "truncated": truncated, "text": content.decode("utf-8", errors="replace")}
            
            return [TextContent(type="text", text=_serialize(res))]
    except Exception as e:
        return [TextContent(type="text", text=_serialize({"error": str(e), "url": url}))]

if __name__ == "__main__":
    async def main():
        async with mcp.server.stdio.stdio_server() as (r, w):
            await server.run(r, w, server.create_initialization_options())
    asyncio.run(main())
