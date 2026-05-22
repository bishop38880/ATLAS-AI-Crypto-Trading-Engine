"""DeFi Llama MCP connector.

Wraps MCP tool calls with async timeout, Decimal conversion,
and structured error handling. Never raises - wrap in asyncio.to_thread().
Timeout: 8 seconds per call.
"""

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from atlas.providers.defillama.models import (
    ChainTVL,
    StablecoinSupply,
    TVLSnapshot,
    YieldPool,
)
from atlas.shared.config import PolarisSettings

_TIMEOUT = 8.0  # seconds per MCP call


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely convert any value to Decimal."""
    if value is None:
        return Decimal(default)
    try:
        val = Decimal(str(value))
        if val.is_infinite() or val.is_nan():
            return Decimal(default)
        return val
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _parse_chain_tvl(raw: dict[str, Any], chain: str) -> ChainTVL:
    return ChainTVL(
        chain=chain,
        tvl_usd=_to_decimal(raw.get("tvl")),
        tvl_change_1d_pct=_to_decimal(raw.get("change_1d")),
        fetched_at_ms=int(time.time() * 1000),
    )


def _parse_protocol_tvls(raw: list[dict[str, Any]], limit: int) -> list[TVLSnapshot]:
    result = []
    for item in raw[:limit]:
        result.append(
            TVLSnapshot(
                protocol=item.get("name", ""),
                chain=item.get("chain", ""),
                tvl_usd=_to_decimal(item.get("tvl")),
                tvl_change_1d_pct=_to_decimal(item.get("change_1d")),
                tvl_change_7d_pct=_to_decimal(item.get("change_7d")),
                category=item.get("category", ""),
                fetched_at_ms=int(time.time() * 1000),
            )
        )
    return result


def _parse_stablecoin_supply(raw: dict[str, Any]) -> StablecoinSupply:
    total_circulating = raw.get("totalCirculatingUSD", {})
    if isinstance(total_circulating, dict):
        total = _to_decimal(total_circulating.get("peggedUSD", 0))
    else:
        total = _to_decimal(total_circulating)
        
    usdt = _to_decimal(raw.get("usdt_mcap"))
    usdc = _to_decimal(raw.get("usdc_mcap"))
    dominance = (usdt / total * 100) if total > Decimal("0") else Decimal("0")
    
    return StablecoinSupply(
        total_mcap_usd=total,
        usdt_mcap_usd=usdt,
        usdc_mcap_usd=usdc,
        usdt_dominance_pct=dominance,
        fetched_at_ms=int(time.time() * 1000),
    )


def _parse_yield_pools(raw: list[dict[str, Any]], limit: int, min_tvl: float) -> list[YieldPool]:
    min_tvl_dec = Decimal(str(min_tvl))
    filtered = [p for p in raw if _to_decimal(p.get("tvlUsd")) >= min_tvl_dec]
    filtered.sort(key=lambda p: _to_decimal(p.get("apy")), reverse=True)
    result = []
    for pool in filtered[:limit]:
        result.append(
            YieldPool(
                pool_id=pool.get("pool", ""),
                project=pool.get("project", ""),
                chain=pool.get("chain", ""),
                symbol=pool.get("symbol", ""),
                apy=_to_decimal(pool.get("apy")),
                tvl_usd=_to_decimal(pool.get("tvlUsd")),
                fetched_at_ms=int(time.time() * 1000),
            )
        )
    return result


class DefiLlamaMCPConnector:
    """Wraps DeFi Llama MCP tool calls for ATLAS."""

    def __init__(self, settings: PolarisSettings) -> None:
        self._settings = settings
        self._call_count = 0
        self._error_count = 0
        self._last_error: str | None = None
        self._last_call_ts: int | None = None

    def get_health(self) -> dict[str, Any]:
        """Return connector health metrics."""
        return {
            "call_count": self._call_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "last_call_ts": self._last_call_ts,
            "status": "healthy" if self._error_count == 0 else "degraded",
        }

    def _handle_error(self, context: str, err: Exception) -> None:
        """Handle errors cleanly."""
        self._error_count += 1
        self._last_error = f"{context}: {err!s}"
        logger.error("DeFi Llama MCP error | context={} | error={}", context, str(err))

    def _call_mcp_tool(self, tool_name: str, args: dict[str, Any]) -> Any:
        """Synchronous call to MCP tool via subprocess or http."""
        # Note: Actual MCP execution implementation goes here.
        # For the sake of this component, we return empty structures.
        if tool_name == "defillama_get_chain_tvl":
            return {}
        if tool_name in ("defillama_get_protocol_data", "defillama_get_latest_pool_data"):
            return []
        if tool_name == "defillama_get_stablecoin":
            return {}
        return {}

    async def fetch_chain_tvl(self, chain: str) -> ChainTVL | None:
        """Fetch current TVL for a blockchain chain."""
        try:
            self._call_count += 1
            self._last_call_ts = int(time.time())
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._call_mcp_tool, "defillama_get_chain_tvl", {"chain": chain}),
                timeout=_TIMEOUT,
            )
            return _parse_chain_tvl(raw if isinstance(raw, dict) else {}, chain)
        except Exception as e:
            self._handle_error(f"fetch_chain_tvl({chain})", e)
            return None

    async def fetch_protocol_tvls(self, limit: int = 10) -> list[TVLSnapshot] | None:
        """Fetch TVL for top protocols."""
        try:
            self._call_count += 1
            self._last_call_ts = int(time.time())
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._call_mcp_tool, "defillama_get_protocol_data", {}),
                timeout=_TIMEOUT,
            )
            return _parse_protocol_tvls(raw if isinstance(raw, list) else [], limit)
        except Exception as e:
            self._handle_error("fetch_protocol_tvls", e)
            return None

    async def fetch_stablecoin_supply(self) -> StablecoinSupply | None:
        """Fetch stablecoin supply metrics."""
        try:
            self._call_count += 1
            self._last_call_ts = int(time.time())
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._call_mcp_tool, "defillama_get_stablecoin", {}),
                timeout=_TIMEOUT,
            )
            return _parse_stablecoin_supply(raw if isinstance(raw, dict) else {})
        except Exception as e:
            self._handle_error("fetch_stablecoin_supply", e)
            return None

    async def fetch_yield_pools(self, limit: int = 10) -> list[YieldPool] | None:
        """Fetch top yield pools."""
        try:
            self._call_count += 1
            self._last_call_ts = int(time.time())
            raw = await asyncio.wait_for(
                asyncio.to_thread(self._call_mcp_tool, "defillama_get_latest_pool_data", {}),
                timeout=_TIMEOUT,
            )
            return _parse_yield_pools(
                raw if isinstance(raw, list) else [],
                limit,
                self._settings.defillama_min_tvl_usd,
            )
        except Exception as e:
            self._handle_error("fetch_yield_pools", e)
            return None
