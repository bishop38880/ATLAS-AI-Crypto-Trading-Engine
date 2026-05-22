"""
FastMCP server: TradFi Macro & Global Liquidity (Fiat Gravity Engine).

MacroCrossMarketAgent - Fiat Gravity Engine: Layer 1 FRED baseline plus Layer 2
USDT/USDC treasury mint-burn telemetry. Background TradFi/Web3 harmonisers feed
typed caches consumed instantly by FastMCP tools.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx
import msgspec
from loguru import logger
from mcp.server.fastmcp import FastMCP
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from .background_sync import (
    get_stablecoin_flows_view,
    get_stablecoin_stamped_pair,
    get_tradfi_observations,
    start_background_sync,
    stop_background_sync,
)
from .macro_engine import calculate_tradfi_state, run_harmonization_pipeline
from .models import StablecoinFlows, TradFiState
from .settings import PredictMacroSettings


_http_client: httpx.AsyncClient | None = None
_w3_singleton: AsyncWeb3 | None = None


def _encode_model(model: Any) -> str:
    """Serialise a Pydantic model to JSON via msgspec."""
    raw_dict: dict[str, Any] = model.model_dump(mode="json")
    return msgspec.json.encode(raw_dict).decode("utf-8")


@asynccontextmanager
async def _lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Wire RPC + httpx clients and start dual background loops."""
    global _http_client, _w3_singleton  # noqa: PLW0603
    settings: PredictMacroSettings = PredictMacroSettings()
    _http_client = httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(45.0, connect=10.0),
        limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
    )
    _w3_singleton = AsyncWeb3(AsyncHTTPProvider(settings.eth_rpc_url))
    logger.info(
        "MacroCrossMarketAgent - Fiat Gravity Engine MCP starting | rpc_prefix={}",
        settings.eth_rpc_url[:28],
    )
    await start_background_sync(
        _w3_singleton,
        settings.fred_api_key,
        _http_client,
    )
    try:
        yield {"settings_loaded": True}
    finally:
        await stop_background_sync()
        if _http_client is not None:
            await _http_client.aclose()
        _http_client = None
        _w3_singleton = None
        logger.info("MacroCrossMarketAgent - Fiat Gravity Engine MCP shutdown")


mcp = FastMCP(
    "polaris-predict-macro",
    instructions=(
        "MacroCrossMarketAgent - Fiat Gravity Engine: merges FRED TradFi "
        "(DXY proxy, US10Y, SOFR, M2) with Ethereum USDT/USDC treasury mint-burn "
        "flows into a harmonised liquidity panel and fiat_gravity_score."
    ),
    lifespan=_lifespan,
)


@mcp.tool()
async def get_tradfi_macro_baseline() -> str:
    """
    Query cached FRED observations for DXY (broad), US10Y, SOFR, and M2.

    MacroCrossMarketAgent - Fiat Gravity Engine baseline Layer 1.
    Returns latest levels and trailing ~30-day percentage changes.
    """
    observations: dict[str, list[tuple[date, Decimal]]] = await get_tradfi_observations()
    state: TradFiState = calculate_tradfi_state(observations)
    return _encode_model(state)


@mcp.tool()
async def get_stablecoin_treasury_flows() -> str:
    """
    Net USD mint-minus-burn for USDT and USDC over 24h, 7d, and 30d windows.

    MacroCrossMarketAgent - Fiat Gravity Engine Layer 2 (Ethereum mainnet).
    Uses exactly six decimals when decoding uint256 amounts.
    """
    flows: StablecoinFlows = await get_stablecoin_flows_view()
    return _encode_model(flows)


@mcp.tool()
async def evaluate_global_liquidity_regime() -> str:
    """
    Master synthesis: Polars-harmonised panel, fiat_gravity_score, regime label.

    MacroCrossMarketAgent - Fiat Gravity Engine: a score near 100.0 indicates
    aggressive global liquidity expansion (TradFi easing plus stablecoin minting),
    mandating maximum risk-on posture for downstream regime agents.
    """
    observations: dict[str, list[tuple[date, Decimal]]] = await get_tradfi_observations()
    usdt_e: list[tuple[datetime, Decimal]]
    usdc_e: list[tuple[datetime, Decimal]]
    usdt_e, usdc_e = await get_stablecoin_stamped_pair()
    combined_events: list[tuple[datetime, Decimal]] = list(usdt_e) + list(usdc_e)

    _panel, report = await asyncio.to_thread(
        run_harmonization_pipeline,
        observations,
        combined_events,
    )
    return _encode_model(report)


if __name__ == "__main__":
    mcp.run(transport="stdio")
