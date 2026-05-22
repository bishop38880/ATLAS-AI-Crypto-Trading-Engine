"""OKX MCP connector — wraps MCP tool calls with async timeout.

Timeout: 5 seconds per call. Never raises — returns None on failure.
All financial values parsed to Decimal immediately at the boundary.
"""

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from atlas.providers.okx_mcp.models import (
    OKXFundingHistory,
    OKXFundingRate,
    OKXFundingRateBar,
    OKXLiquidationOrder,
    OKXLiquidationSnapshot,
    OKXLongShortRatio,
    OKXOpenInterest,
    OKXOpenInterestBar,
    OKXOpenInterestHistory,
)

_TIMEOUT = 5.0  # seconds per MCP call


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


def normalise_to_okx_instid(symbol: str) -> str:
    """Convert POLARIS symbol format to OKX instId format.

    "BTCUSDT"    → "BTC-USDT-SWAP"
    "1INCHUSDT"  → "1INCH-USDT-SWAP"
    "RENDERUSDT" → "RENDER-USDT-SWAP"

    Rule: strip "USDT" suffix, insert "-USDT-SWAP".
    """
    if symbol.upper().endswith("USDT"):
        base = symbol[: -len("USDT")].upper()
        return f"{base}-USDT-SWAP"
    return f"{symbol.upper()}-USDT-SWAP"


def _parse_funding_rate(raw: dict[str, Any], inst_id: str) -> OKXFundingRate:
    """Parse raw MCP response into OKXFundingRate."""
    return OKXFundingRate(
        inst_id=inst_id,
        funding_rate=_to_decimal(raw.get("fundingRate")),
        funding_time=int(raw.get("fundingTime", 0)),
        next_funding_time=int(raw.get("nextFundingTime", 0)),
        min_funding_rate=_to_decimal(raw.get("minFundingRate")),
        max_funding_rate=_to_decimal(raw.get("maxFundingRate")),
        fetched_at_ms=int(time.time() * 1000),
    )


def _parse_funding_history_bars(
    raw_list: list[dict[str, Any]], inst_id: str,
) -> list[OKXFundingRateBar]:
    """Parse a list of raw funding rate history entries."""
    bars: list[OKXFundingRateBar] = []
    for item in raw_list:
        bars.append(OKXFundingRateBar(
            inst_id=inst_id,
            funding_rate=_to_decimal(item.get("fundingRate")),
            funding_time=int(item.get("fundingTime", 0)),
            realized_rate=_to_decimal(item.get("realizedRate")),
        ))
    return bars


def _parse_oi(raw: dict[str, Any], inst_id: str) -> OKXOpenInterest:
    """Parse raw MCP response into OKXOpenInterest."""
    return OKXOpenInterest(
        inst_id=inst_id,
        oi=_to_decimal(raw.get("oi")),
        oi_ccy=_to_decimal(raw.get("oiCcy")),
        ts=int(raw.get("ts", 0)),
    )


def _parse_oi_history_bars(
    raw_list: list[dict[str, Any]], inst_id: str,
) -> list[OKXOpenInterestBar]:
    """Parse a list of raw OI history entries."""
    bars: list[OKXOpenInterestBar] = []
    for item in raw_list:
        bars.append(OKXOpenInterestBar(
            inst_id=inst_id,
            oi=_to_decimal(item.get("oi")),
            oi_ccy=_to_decimal(item.get("oiCcy")),
            ts=int(item.get("ts", 0)),
        ))
    return bars


def _parse_ls_ratio(raw: dict[str, Any], inst_id: str) -> OKXLongShortRatio:
    """Parse raw MCP response into OKXLongShortRatio."""
    return OKXLongShortRatio(
        inst_id=inst_id,
        long_short_ratio=_to_decimal(raw.get("longShortRatio")),
        long_ratio=_to_decimal(raw.get("longRatio")),
        short_ratio=_to_decimal(raw.get("shortRatio")),
        ts=int(raw.get("ts", 0)),
    )


def _parse_liquidation_orders(
    raw_list: list[dict[str, Any]], inst_id: str,
) -> list[OKXLiquidationOrder]:
    """Parse a list of raw liquidation order entries."""
    orders: list[OKXLiquidationOrder] = []
    for item in raw_list:
        orders.append(OKXLiquidationOrder(
            inst_id=item.get("instId", inst_id),
            side=item.get("side", ""),
            size=_to_decimal(item.get("sz")),
            bk_px=_to_decimal(item.get("bkPx")),
            ts=int(item.get("ts", 0)),
        ))
    return orders


class OKXMCPConnector:
    """Wraps OKX MCP tool calls for ATLAS derivatives data."""

    def __init__(self, mcp_url: str) -> None:
        """Initialise with MCP server URL.

        Args:
            mcp_url: OKX MCP server endpoint.
        """
        self._mcp_url = mcp_url
        self._call_count = 0
        self._error_count = 0
        self._last_error: str | None = None
        self._last_call_ts: int | None = None

    def _record_call(self) -> None:
        """Track call metrics."""
        self._call_count += 1
        self._last_call_ts = int(time.time())

    def _record_error(self, context: str, err: Exception) -> None:
        """Track error metrics and log."""
        self._error_count += 1
        self._last_error = f"{context}: {err!s}"
        logger.error(
            "OKX MCP error | provider=okx_mcp | op={} | error={}",
            context, str(err),
        )

    def get_health(self) -> dict[str, Any]:
        """Return connector health metrics."""
        status = "healthy" if self._error_count == 0 else "degraded"
        return {
            "status": status,
            "last_call_ts": self._last_call_ts,
            "last_error": self._last_error,
            "call_count": self._call_count,
            "error_count": self._error_count,
        }

    async def _call_mcp_tool(
        self, tool_name: str, args: dict[str, Any],
    ) -> Any:
        """Call MCP tool via SDK. Placeholder for actual MCP integration."""
        # Actual MCP SDK call goes here. Returns raw dict/list.
        return {}

    async def fetch_funding_rate(self, inst_id: str) -> OKXFundingRate | None:
        """Fetch current funding rate for instrument."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_funding_rate", {"instId": inst_id},
                ),
                timeout=_TIMEOUT,
            )
            return _parse_funding_rate(
                raw if isinstance(raw, dict) else {}, inst_id,
            )
        except Exception as e:
            self._record_error(f"fetch_funding_rate({inst_id})", e)
            return None

    async def fetch_funding_history(
        self, inst_id: str, limit: int = 90,
    ) -> OKXFundingHistory | None:
        """Fetch funding rate history (90 bars = 30 days)."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_funding_rate_history",
                    {"instId": inst_id, "limit": limit},
                ),
                timeout=_TIMEOUT,
            )
            bars = _parse_funding_history_bars(
                raw if isinstance(raw, list) else [], inst_id,
            )
            return OKXFundingHistory(inst_id=inst_id, bars=bars)
        except Exception as e:
            self._record_error(f"fetch_funding_history({inst_id})", e)
            return None

    async def fetch_open_interest(
        self, inst_id: str,
    ) -> OKXOpenInterest | None:
        """Fetch current open interest."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_open_interest",
                    {"instId": inst_id, "period": "5m"},
                ),
                timeout=_TIMEOUT,
            )
            return _parse_oi(
                raw if isinstance(raw, dict) else {}, inst_id,
            )
        except Exception as e:
            self._record_error(f"fetch_open_interest({inst_id})", e)
            return None

    async def fetch_oi_history(
        self, inst_id: str, limit: int = 336,
    ) -> OKXOpenInterestHistory | None:
        """Fetch OI history (336 bars = 14 days at 1h)."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_open_interest_history",
                    {"instId": inst_id, "period": "1H", "limit": limit},
                ),
                timeout=_TIMEOUT,
            )
            bars = _parse_oi_history_bars(
                raw if isinstance(raw, list) else [], inst_id,
            )
            return OKXOpenInterestHistory(inst_id=inst_id, bars=bars)
        except Exception as e:
            self._record_error(f"fetch_oi_history({inst_id})", e)
            return None

    async def fetch_long_short_ratio(
        self, inst_id: str,
    ) -> OKXLongShortRatio | None:
        """Fetch current long/short ratio."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_long_short_ratio",
                    {"instId": inst_id, "period": "5m"},
                ),
                timeout=_TIMEOUT,
            )
            return _parse_ls_ratio(
                raw if isinstance(raw, dict) else {}, inst_id,
            )
        except Exception as e:
            self._record_error(f"fetch_long_short_ratio({inst_id})", e)
            return None

    async def fetch_liquidations(
        self, inst_id: str, uly: str,
    ) -> OKXLiquidationSnapshot | None:
        """Fetch recent filled liquidation orders."""
        try:
            self._record_call()
            raw = await asyncio.wait_for(
                self._call_mcp_tool(
                    "get_liquidation_orders",
                    {
                        "instType": "SWAP",
                        "uly": uly,
                        "state": "filled",
                        "limit": 100,
                    },
                ),
                timeout=_TIMEOUT,
            )
            orders = _parse_liquidation_orders(
                raw if isinstance(raw, list) else [], inst_id,
            )
            return OKXLiquidationSnapshot(
                inst_id=inst_id,
                orders=orders,
                fetched_at=int(time.time() * 1000),
            )
        except Exception as e:
            self._record_error(f"fetch_liquidations({inst_id})", e)
            return None
