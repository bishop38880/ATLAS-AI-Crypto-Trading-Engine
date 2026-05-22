"""DuneMCPProvider — Tier 2 on-chain token metrics via MCP SSE transport.

Connects to the Dune MCP server using the official SDK's SSE client
(``mcp.client.sse.sse_client`` + ``mcp.client.session.ClientSession``).
Returns ``DuneSnapshot`` Pydantic objects — NEVER ``.model_dump()``.

Sentinel v3.0 invariants enforced:
  - Decimal for all USD / percentage fields
  - Loguru structured logging (no f-strings)
  - ``msgspec.json.decode`` for JSON parsing
  - ``asyncio.Semaphore(3)`` concurrency cap (credit-burn prevention)
  - Graceful degradation: SSE failures → OFFLINE status, never crash
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import TextContent

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.dune.models import (
    DEXVolumeData,
    DuneSnapshot,
    PerpOIData,
    StablecoinFlowData,
    TokenUnlockEvent,
    WhaleFlowData,
)
from atlas.shared.config import PolarisSettings

# Concurrency cap — Dune is credit-based; prevent flood.
_MAX_CONCURRENT = 3


def _safe_decimal(val: Any) -> Decimal:
    """Cast a raw value to Decimal safely."""
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return Decimal("0")


class DuneMCPProvider(BaseProvider):
    """Tier 2 Dune Analytics token metrics — MCP SSE adapter.

    PRIMARY CONSUMER: OnChainIntelligenceAgent (protocol TVL, token velocity).
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
    ) -> None:
        """Initialise Dune MCP provider.

        Args:
            redis_client: Shared async Redis connection.
            settings: Configuration with dune_mcp_url and timeouts.
        """
        super().__init__(
            provider_name="dune_mcp",
            redis_client=redis_client,
            max_concurrent=_MAX_CONCURRENT,
        )
        self._mcp_url = settings.dune_mcp_url
        self._timeout = settings.dune_query_timeout_seconds
        self._ttl = settings.dune_ttl_seconds
        self._last_success: float = 0.0

    # ------------------------------------------------------------------
    # BaseProvider interface
    # ------------------------------------------------------------------

    async def get_health_status(self) -> ProviderHealth:
        """Return immutable health snapshot."""
        return ProviderHealth(
            name=self._provider_name,
            status=self._status,
            last_update=self._last_success,
            error=self._last_error,
        )

    async def close(self) -> None:
        """No persistent connections — SSE is ephemeral per call."""
        logger.info("dune_provider_close | provider=dune_mcp")

    # ------------------------------------------------------------------
    # MCP SSE tool call
    # ------------------------------------------------------------------

    async def _call_mcp_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Connect via SSE, call a single tool, parse with msgspec."""
        if self._status == "OFFLINE":
            return {}

        async with self._semaphore:
            try:
                return await asyncio.wait_for(
                    self._execute_sse_call(tool_name, arguments),
                    timeout=self._timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "dune_mcp_timeout | tool={} | timeout_s={}",
                    tool_name,
                    self._timeout,
                )
                self.mark_degraded("SSE timeout on {}".format(tool_name))
                return {}
            except Exception as exc:
                logger.warning(
                    "dune_mcp_error | tool={} | error={}",
                    tool_name,
                    exc,
                )
                self.mark_degraded(str(exc))
                return {}

    async def _execute_sse_call(
        self,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Establish SSE session, call tool, return parsed dict."""
        async with sse_client(
            url=self._mcp_url,
            timeout=self._timeout,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                if result.isError:
                    logger.warning(
                        "dune_tool_error | tool={} | content={}",
                        tool_name,
                        result.content,
                    )
                    return {}

                return self._parse_tool_result(result.content)

    def _parse_tool_result(
        self,
        content: list[Any],
    ) -> dict[str, Any]:
        """Extract JSON from MCP TextContent using msgspec."""
        for item in content:
            if isinstance(item, TextContent):
                raw: dict[str, Any] = msgspec.json.decode(
                    item.text.encode("utf-8"),
                    type=dict,
                )
                self._last_success = time.monotonic()
                self.mark_healthy()
                return raw
        return {}

    # ------------------------------------------------------------------
    # Domain fetch methods
    # ------------------------------------------------------------------

    async def fetch_dex_volume(self, asset: str) -> DEXVolumeData:
        """Fetch DEX volume metrics for an asset."""
        data = await self._call_mcp_tool("get_dex_volume", {"asset": asset})
        if not data:
            return DEXVolumeData()
        return DEXVolumeData(
            volume_24h_usd=_safe_decimal(data.get("volume_24h_usd")),
            volume_7d_usd=_safe_decimal(data.get("volume_7d_usd")),
            market_share_pct=_safe_decimal(data.get("market_share_pct")),
        )

    async def fetch_stablecoin_flow(self, asset: str) -> StablecoinFlowData:
        """Fetch stablecoin net flow data."""
        data = await self._call_mcp_tool("get_stablecoin_flow", {"asset": asset})
        if not data:
            return StablecoinFlowData()
        return StablecoinFlowData(
            net_flow_24h_usd=_safe_decimal(data.get("net_flow_24h_usd")),
            net_flow_7d_usd=_safe_decimal(data.get("net_flow_7d_usd")),
            total_supply_usd=_safe_decimal(data.get("total_supply_usd")),
        )

    async def fetch_perp_oi(self, asset: str) -> PerpOIData:
        """Fetch perpetual futures open interest data."""
        data = await self._call_mcp_tool("get_perp_oi", {"asset": asset})
        if not data:
            return PerpOIData()
        return PerpOIData(
            total_oi_usd=_safe_decimal(data.get("total_oi_usd")),
            oi_change_24h_pct=_safe_decimal(data.get("oi_change_24h_pct")),
        )

    async def fetch_whale_flow(self, asset: str) -> WhaleFlowData:
        """Fetch whale accumulation/distribution data."""
        data = await self._call_mcp_tool("get_whale_flow", {"asset": asset})
        if not data:
            return WhaleFlowData()
        return WhaleFlowData(
            net_flow_24h_usd=_safe_decimal(data.get("net_flow_24h_usd")),
            direction=data.get("direction", "NEUTRAL"),
            whale_dominance_pct=_safe_decimal(data.get("whale_dominance_pct")),
        )

    async def fetch_token_unlocks(self, asset: str) -> list[TokenUnlockEvent]:
        """Fetch upcoming token unlock events."""
        data = await self._call_mcp_tool("get_token_unlocks", {"asset": asset})
        if not data:
            return []
        raw_list = data.get("unlocks", [])
        if not isinstance(raw_list, list):
            return []
        events: list[TokenUnlockEvent] = []
        for item in raw_list:
            if isinstance(item, dict):
                events.append(TokenUnlockEvent(
                    asset=str(item.get("asset", asset)),
                    unlock_pct_of_supply=_safe_decimal(item.get("unlock_pct_of_supply")),
                    unlock_usd_value=_safe_decimal(item.get("unlock_usd_value")),
                    days_until_unlock=int(item.get("days_until_unlock", 0)),
                    unlock_type=item.get("unlock_type", "UNKNOWN"),
                ))
        return events

    # ------------------------------------------------------------------
    # Public API — fetch_data (BaseProvider contract)
    # ------------------------------------------------------------------

    async def fetch_data(self, asset: str) -> DuneSnapshot:
        """Assemble full DuneSnapshot via concurrent SSE calls.

        Returns the Pydantic object directly — NEVER .model_dump().
        """
        results = await asyncio.gather(
            self.fetch_dex_volume(asset),
            self.fetch_stablecoin_flow(asset),
            self.fetch_perp_oi(asset),
            self.fetch_whale_flow(asset),
            self.fetch_token_unlocks(asset),
            return_exceptions=True,
        )

        dex = self._unwrap(results[0], DEXVolumeData())
        stable = self._unwrap(results[1], StablecoinFlowData())
        perp = self._unwrap(results[2], PerpOIData())
        whale = self._unwrap(results[3], WhaleFlowData())
        unlocks = self._unwrap(results[4], [])

        degraded = self._status != "HEALTHY"

        return DuneSnapshot(
            asset=asset,
            dex_volume=dex,
            stablecoin_flow=stable,
            perp_oi=perp,
            whale_flow=whale,
            token_unlocks=unlocks if isinstance(unlocks, list) else [],
            stale=degraded,
            status="degraded" if degraded else "healthy",
        )

    @staticmethod
    def _unwrap(result: Any, fallback: Any) -> Any:
        """Unwrap gather result, returning fallback on exception."""
        if isinstance(result, BaseException):
            logger.warning("dune_gather_exception | error={}", result)
            return fallback
        return result
