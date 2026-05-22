"""
FastMCP server for Proof of Reserve & RWA Macro rotation signals.

Section 5 Architecture: Macro Context — Institutional Rotation.
Exposes three tools for agentic consumption by ``MarketRegimeAgent``
and ``MacroCrossMarketAgent``:

- ``get_por_health`` — Chainlink PoR vs on-chain supply, de-peg flags.
- ``get_rwa_flows`` — 24h / 7d mint/burn volume from the in-memory cache.
- ``evaluate_macro_rotation`` — Master aggregation into a regime signal.

The background poller is launched on server startup via the FastMCP
lifespan hook. All tool calls read instantly from the in-memory cache.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import msgspec
from loguru import logger
from mcp.server.fastmcp import FastMCP
from web3 import AsyncWeb3
from web3.providers import AsyncHTTPProvider

from .background_poller import (
    get_all_cached,
    get_cached_flows,
    start_poller,
    stop_poller,
)
from .config import (
    ROTATION_VELOCITY_THRESHOLD,
    RWA_CONTRACTS,
    SUPPORTED_SYMBOLS,
    UNDERCOLLATERALIZATION_THRESHOLD,
)
from .models import (
    MacroRotationReport,
    PoRHealthReport,
    RegimeSignal,
    RWAFlows,
    RWATokenSummary,
)


# ---------------------------------------------------------------------------
# Web3 provider singleton
# ---------------------------------------------------------------------------
_w3: AsyncWeb3 | None = None


def _get_rpc_url() -> str:
    """
    Read the Ethereum RPC URL from environment.

    NOTE: MCP servers run as isolated stdio processes outside the ATLAS
    application boundary. PolarisSettings is not available in this context.
    os.environ is the accepted pattern for MCP server processes — see
    decision_log/db.py and timescaledb/db.py for precedent.
    """
    rpc_url: str = os.environ.get(
        "ETH_RPC_URL", "https://eth-mainnet.g.alchemy.com/v2/demo"
    )
    return rpc_url


def _init_web3() -> AsyncWeb3:
    """Initialise the async Web3 provider singleton."""
    global _w3  # noqa: PLW0603
    if _w3 is None:
        rpc_url: str = _get_rpc_url()
        _w3 = AsyncWeb3(AsyncHTTPProvider(rpc_url))
        logger.info("Web3 provider initialised | rpc={}", rpc_url[:40] + "…")
    return _w3


# ---------------------------------------------------------------------------
# FastMCP lifespan — start/stop the background poller
# ---------------------------------------------------------------------------
@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Start the background poller on server startup, stop on shutdown."""
    w3: AsyncWeb3 = _init_web3()
    await start_poller(w3)
    logger.info("PoR/RWA Macro MCP server ready")
    try:
        yield {"w3": w3}
    finally:
        await stop_poller()
        logger.info("PoR/RWA Macro MCP server shutdown complete")


# ---------------------------------------------------------------------------
# FastMCP instance
# ---------------------------------------------------------------------------
mcp = FastMCP(
    "polaris-por-rwa-macro",
    instructions=(
        "Section 5 Architecture: Macro Context — Institutional Rotation. "
        "Monitors Chainlink PoR feeds and RWA mint/burn flows for "
        "institutional flight-to-safety rotation detection."
    ),
    lifespan=_lifespan,
)



# ---------------------------------------------------------------------------
# Tool: get_por_health
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_por_health(symbol: str) -> str:
    """
    Get Chainlink Proof of Reserve health for a supported RWA token.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Returns collateralisation ratio, de-peg flag, and oracle staleness.
    Consumed by ``MarketRegimeAgent`` as a solvency signal.

    Args:
        symbol: Canonical RWA symbol (BUIDL, USDY, OUSG).
    """
    validated: str = _validate_symbol(symbol)
    cached: dict[str, Any] | None = await get_cached_flows(validated)
    if cached is None:
        return _encode_error(validated, "Cache not yet populated — poller warming up")

    report: PoRHealthReport = _build_por_report(validated, cached)
    return _encode_model(report)


def _build_por_report(
    symbol: str, cached: dict[str, Any]
) -> PoRHealthReport:
    """Construct a PoRHealthReport from cached data."""
    contract_info: dict[str, str | int] = RWA_CONTRACTS[symbol]
    return PoRHealthReport(
        symbol=symbol,
        name=str(contract_info["name"]),
        on_chain_supply=cached.get("total_supply", Decimal("0")),
        off_chain_reserve=cached.get("off_chain_reserve", Decimal("0")),
        collateralization_ratio=cached.get(
            "collateralization_ratio", Decimal("0")
        ),
        is_fully_backed=cached.get("is_fully_backed", False),
        oracle_updated_at=cached.get(
            "oracle_updated_at", datetime.now(tz=timezone.utc)
        ),
        oracle_stale=cached.get("oracle_stale", True),
        status=cached.get("status", "DEGRADED"),
    )


# ---------------------------------------------------------------------------
# Tool: get_rwa_flows
# ---------------------------------------------------------------------------
@mcp.tool()
async def get_rwa_flows(symbol: str) -> str:
    """
    Get aggregated 24h and 7d mint/burn flow for a supported RWA token.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Positive net_flow indicates minting dominance (capital inflow).
    Consumed by ``MacroCrossMarketAgent`` for velocity-based rotation detection.

    Args:
        symbol: Canonical RWA symbol (BUIDL, USDY, OUSG).
    """
    validated: str = _validate_symbol(symbol)
    cached: dict[str, Any] | None = await get_cached_flows(validated)
    if cached is None:
        return _encode_error(validated, "Cache not yet populated — poller warming up")

    report: RWAFlows = _build_flow_report(validated, cached)
    return _encode_model(report)


def _build_flow_report(symbol: str, cached: dict[str, Any]) -> RWAFlows:
    """Construct an RWAFlows report from cached data."""
    return RWAFlows(
        symbol=symbol,
        mint_volume_24h=cached.get("mint_volume_24h", Decimal("0")),
        burn_volume_24h=cached.get("burn_volume_24h", Decimal("0")),
        net_flow_24h=cached.get("net_flow_24h", Decimal("0")),
        mint_volume_7d=cached.get("mint_volume_7d", Decimal("0")),
        burn_volume_7d=cached.get("burn_volume_7d", Decimal("0")),
        net_flow_7d=cached.get("net_flow_7d", Decimal("0")),
        avg_daily_net_flow_7d=cached.get("avg_daily_net_flow_7d", Decimal("0")),
        status=cached.get("status", "DEGRADED"),
    )


# ---------------------------------------------------------------------------
# Tool: evaluate_macro_rotation
# ---------------------------------------------------------------------------
@mcp.tool()
async def evaluate_macro_rotation() -> str:
    """
    Master aggregation: synthesise all RWA flows into a macro regime signal.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    Outputs a deterministic ``regime_signal`` (RISK_ON, RISK_OFF, NEUTRAL,
    DEGRADED) based on the velocity of RWA minting across all tracked tokens.
    Consumed by ``MarketRegimeAgent`` for portfolio regime classification.
    """
    all_cached: dict[str, dict[str, Any]] = await get_all_cached()
    if not all_cached:
        return _encode_degraded_rotation("No cached data — poller warming up")

    summaries: list[RWATokenSummary] = []
    total_24h: Decimal = Decimal("0")
    total_7d: Decimal = Decimal("0")

    for symbol, data in all_cached.items():
        summary: RWATokenSummary = _build_token_summary(symbol, data)
        summaries.append(summary)
        total_24h += data.get("net_flow_24h", Decimal("0"))
        total_7d += data.get("net_flow_7d", Decimal("0"))

    velocity: Decimal = _compute_aggregate_velocity(total_24h, total_7d)
    signal, reasoning = _determine_regime(
        velocity, total_24h, summaries
    )

    report: MacroRotationReport = MacroRotationReport(
        regime_signal=signal,
        total_net_inflow_24h=total_24h,
        total_net_inflow_7d=total_7d,
        velocity_ratio_aggregate=velocity,
        token_summaries=summaries,
        reasoning=reasoning,
    )
    return _encode_model(report)


def _build_token_summary(
    symbol: str, data: dict[str, Any]
) -> RWATokenSummary:
    """Build a per-token summary for the rotation report."""
    net_24h: Decimal = data.get("net_flow_24h", Decimal("0"))
    avg_daily: Decimal = data.get("avg_daily_net_flow_7d", Decimal("0"))
    velocity: Decimal = (
        net_24h / avg_daily if avg_daily > Decimal("0") else Decimal("0")
    )
    return RWATokenSummary(
        symbol=symbol,
        net_flow_24h=net_24h,
        net_flow_7d=data.get("net_flow_7d", Decimal("0")),
        velocity_ratio=velocity,
        is_fully_backed=data.get("is_fully_backed", False),
    )


def _compute_aggregate_velocity(
    total_24h: Decimal, total_7d: Decimal
) -> Decimal:
    """Compute aggregate velocity ratio: 24h flow / avg daily 7d."""
    avg_daily_7d: Decimal = total_7d / Decimal("7")
    if avg_daily_7d <= Decimal("0"):
        return Decimal("0")
    return total_24h / avg_daily_7d


def _determine_regime(
    velocity: Decimal,
    total_24h: Decimal,
    summaries: list[RWATokenSummary],
) -> tuple[RegimeSignal, str]:
    """
    Determine the macro regime signal from velocity and flow data.

    Section 5 Architecture: Macro Context — Institutional Rotation.
    """
    underbacked: list[str] = [
        s.symbol for s in summaries if not s.is_fully_backed
    ]
    if underbacked:
        return (
            RegimeSignal.RISK_OFF,
            f"Undercollateralised tokens detected: {underbacked}. "
            "Systemic de-peg risk flagged.",
        )

    threshold: Decimal = Decimal(str(ROTATION_VELOCITY_THRESHOLD))
    if velocity >= threshold and total_24h > Decimal("0"):
        return (
            RegimeSignal.RISK_OFF,
            f"RWA minting velocity {velocity:.2f}x exceeds {threshold}x threshold. "
            "Institutional capital rotating into tokenised Treasuries.",
        )

    if total_24h > Decimal("0") and velocity > Decimal("1"):
        return (
            RegimeSignal.NEUTRAL,
            f"Moderate RWA inflow detected (velocity={velocity:.2f}x). "
            "No anomalous rotation signal.",
        )

    return (
        RegimeSignal.RISK_ON,
        f"Low or negative RWA flows (velocity={velocity:.2f}x). "
        "Capital not seeking Treasury safety — risk appetite intact.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _validate_symbol(symbol: str) -> str:
    """Validate and normalise an RWA symbol."""
    normalised: str = symbol.upper().strip()
    if normalised not in SUPPORTED_SYMBOLS:
        raise ValueError(
            f"Unsupported symbol '{normalised}'. "
            f"Supported: {sorted(SUPPORTED_SYMBOLS)}"
        )
    return normalised


def _encode_model(model: Any) -> str:
    """Serialise a Pydantic model to JSON string via msgspec."""
    raw_dict: dict[str, Any] = model.model_dump(mode="json")
    return msgspec.json.encode(raw_dict).decode("utf-8")


def _encode_error(symbol: str, message: str) -> str:
    """Return a JSON error response."""
    payload: dict[str, str] = {"symbol": symbol, "error": message, "status": "DEGRADED"}
    return msgspec.json.encode(payload).decode("utf-8")


def _encode_degraded_rotation(message: str) -> str:
    """Return a degraded MacroRotationReport as JSON."""
    report: MacroRotationReport = MacroRotationReport(
        regime_signal=RegimeSignal.DEGRADED,
        total_net_inflow_24h=Decimal("0"),
        total_net_inflow_7d=Decimal("0"),
        velocity_ratio_aggregate=Decimal("0"),
        token_summaries=[],
        reasoning=message,
        status="DEGRADED",
    )
    return _encode_model(report)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    mcp.run(transport="stdio")
