"""Nansen Intelligence MCP Server — FastMCP entry point.

Hardened gateway for Smart Money \u0026 Wallet Intelligence.
Provides credit budget management and rate limiting.
"""

from __future__ import annotations

import os
from decimal import Decimal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from loguru import logger
from mcp.server.fastmcp import FastMCP

from mcp_servers.nansen.client import CreditGovernor, NansenClient
from mcp_servers.nansen.models import (
    ExchangeNetflow,
    SmartMoneyFlow,
    SmartMoneyHolder,
    WalletProfile,
    encode_model,
)
import asyncio

# ─── Bootstrap ───────────────────────────────────────────────────────────────

_NANSEN_API_KEY = os.environ.get("NANSEN_API_KEY", "")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


# ─── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Initialise and cleanup Nansen client resources."""
    global _client, _governor  # noqa: PLW0603
    
    _governor = CreditGovernor(redis_url=_REDIS_URL)
    _client = NansenClient(
        api_key=_NANSEN_API_KEY,
        redis_url=_REDIS_URL,
        governor=_governor,
    )
    
    logger.info("Nansen MCP ready | credits=100k/mo | budget_governor=ACTIVE")
    try:
        yield {}
    finally:
        await _client.close()
        await _governor.close()
        logger.info("Nansen MCP shutdown complete")


# ─── FastMCP Instance ───────────────────────────────────────────────────────

mcp = FastMCP(
    "Nansen_Intelligence",
    instructions=(
        "Hardened gateway for Nansen Smart Money \u0026 Wallet Intelligence. "
        "Enforces credit budget limits and sliding-window rate limiting. "
        "Normalises all USD fields to Decimal for ATLAS engine consumption."
    ),
    lifespan=_lifespan,
)

_client: NansenClient | None = None
_governor: CreditGovernor | None = None


# ─── Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
async def get_token_smart_money_flow(
    token_address: str,
    chain: str = "ethereum",
    time_range: str = "24h",
) -> str:
    """Fetch net USD flow from Nansen-labelled smart money wallets.
    
    Args:
        token_address: Contract address of the token.
        chain: Network identifier (default 'ethereum').
        time_range: Analysis window ('24h' or '7d').
    """
    if _client is None:
        return "Nansen client not initialised"

    data = await _client.get_token_smart_money_flow(token_address, chain, time_range)
    if not data:
        return "Failed to fetch smart money flow (budget exceeded or API error)"

    model = SmartMoneyFlow(
        asset=data.get("asset", "UNKNOWN"),
        chain=chain,
        net_flow_usd=Decimal(str(data.get("net_flow_usd", "0"))),
        unique_smart_wallets=int(data.get("unique_smart_wallets", 0)),
        time_range=time_range,
    )
    return encode_model(model)


@mcp.tool()
async def get_exchange_netflow(
    token_address: str,
    chain: str = "ethereum",
    time_range: str = "24h",
) -> str:
    """Fetch net token movement to/from centralised exchanges.
    
    Negative values indicate outflows (bullish supply shock).
    
    Args:
        token_address: Contract address of the token.
        chain: Network identifier (default 'ethereum').
        time_range: Analysis window ('24h' or '7d').
    """
    if _client is None:
        return "Nansen client not initialised"

    data = await _client.get_exchange_netflow(token_address, chain, time_range)
    if not data:
        return "Failed to fetch exchange netflow"

    model = ExchangeNetflow(
        asset=data.get("asset", "UNKNOWN"),
        netflow_usd=Decimal(str(data.get("net_flow_usd", "0"))),
        inflow_usd=Decimal(str(data.get("inflow_usd", "0"))),
        outflow_usd=Decimal(str(data.get("outflow_usd", "0"))),
    )
    return encode_model(model)


@mcp.tool()
async def get_smart_money_top_holders(
    token_address: str,
    chain: str = "ethereum",
) -> str:
    """Fetch top smart money holders and their current positions.
    
    Args:
        token_address: Contract address of the token.
        chain: Network identifier (default 'ethereum').
    """
    if _client is None:
        return "Nansen client not initialised"

    data = await _client.get_smart_money_top_holders(token_address, chain)
    if not data:
        return "Failed to fetch smart money holders"

    holders = [
        SmartMoneyHolder(
            wallet_address=h.get("address", ""),
            label=h.get("label", "UNKNOWN"),
            balance_token=Decimal(str(h.get("balance_token", "0"))),
            balance_usd=Decimal(str(h.get("balance_usd", "0"))),
        )
        for h in data.get("holders", [])
    ]
    return encode_model(holders)


@mcp.tool()
async def get_wallet_profile(
    wallet_address: str,
) -> str:
    """Fetch comprehensive profile and labels for a specific wallet.
    
    Args:
        wallet_address: Blockchain address to profile.
    """
    if _client is None:
        return "Nansen client not initialised"

    # Fetch labels and portfolio in parallel
    labels_task = _client.get_wallet_labels(wallet_address)
    portfolio_task = _client.get_wallet_portfolio(wallet_address)
    
    labels_data, portfolio_data = await asyncio.gather(labels_task, portfolio_task)
    
    if not labels_data or not portfolio_data:
        return "Failed to fetch complete wallet profile"

    model = WalletProfile(
        address=wallet_address,
        labels=labels_data.get("labels", []),
        total_value_usd=Decimal(str(portfolio_data.get("total_value_usd", "0"))),
        primary_chain=portfolio_data.get("primary_chain", "ethereum"),
    )
    return encode_model(model)


# ─── Entry Point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
