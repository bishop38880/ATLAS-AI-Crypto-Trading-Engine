"""NansenProvider — Tier 2 smart money intelligence via MCP SSE transport.

Connects to Nansen's remote MCP server using the official SDK's SSE client
(``mcp.client.sse.sse_client`` + ``mcp.client.session.ClientSession``).
Returns ``NansenSnapshot`` Pydantic objects — NEVER ``.model_dump()``.

Sentinel v3.0 invariants enforced:
  - Decimal for all USD fields
  - Loguru structured logging (no f-strings)
  - ``msgspec.json.decode`` for JSON parsing
  - ``asyncio.Semaphore(3)`` concurrency cap
  - Graceful degradation: SSE failures → stale snapshot, never crash
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import msgspec
from loguru import logger
from mcp.client.session import ClientSession
from mcp.client.sse import sse_client
from mcp.types import TextContent

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.nansen.models import (
    ExchangeNetflow,
    NansenSnapshot,
    SmartMoneyFlow,
)
from atlas.providers.nansen.tool_map import NANSEN_CHAINS, TOKEN_ADDRESSES
from atlas.shared.config import PolarisSettings

import redis.asyncio as redis_async

# Concurrency cap — Nansen is credit-based; prevent flood.
_MAX_CONCURRENT = 3


def _safe_decimal(val: Any) -> Decimal:
    """Cast a raw value to Decimal safely."""
    if val is None:
        return Decimal("0")
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return Decimal("0")


class NansenProvider(BaseProvider):
    """Tier 2 Nansen smart money intelligence — MCP SSE adapter.

    PRIMARY CONSUMER: OnChainAgent (smart money accumulation signal).
    SECONDARY CONSUMER: WhaleAgent (top holder position changes).
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
    ) -> None:
        super().__init__(
            provider_name="nansen",
            redis_client=redis_client,
            max_concurrent=_MAX_CONCURRENT,
        )
        self._mcp_url = settings.nansen_mcp_url
        api_key = settings.nansen_api_key.get_secret_value()
        self._api_key: str | None = api_key if api_key else None
        self._ttl = settings.nansen_ttl_seconds
        self._timeout = settings.nansen_query_timeout_seconds
        self._last_success: float = 0.0

        if not self._api_key:
            logger.warning(
                "nansen_provider_init | status=UNAVAILABLE | reason=missing_api_key"
            )
            self._status = "OFFLINE"

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
        logger.info("nansen_provider_close | provider=nansen")

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
                    "nansen_mcp_timeout | tool={} | timeout_s={}",
                    tool_name,
                    self._timeout,
                )
                self.mark_degraded("SSE timeout on {}".format(tool_name))
                return {}
            except Exception as exc:
                logger.warning(
                    "nansen_mcp_error | tool={} | error={}",
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
        headers = self._build_headers()

        async with sse_client(
            url=self._mcp_url,
            headers=headers,
            timeout=self._timeout,
        ) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                result = await session.call_tool(tool_name, arguments)

                if result.isError:
                    logger.warning(
                        "nansen_tool_error | tool={} | content={}",
                        tool_name,
                        result.content,
                    )
                    return {}

                return self._parse_tool_result(result.content)

    def _build_headers(self) -> dict[str, Any]:
        """Construct auth headers for Nansen SSE endpoint."""
        headers: dict[str, Any] = {}
        if self._api_key:
            headers["Authorization"] = "Bearer {}".format(self._api_key)
        return headers

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

    async def fetch_smart_money_flow(
        self,
        asset: str,
        time_range: str = "24h",
    ) -> SmartMoneyFlow:
        """Fetch net smart money flow via MCP SSE tool call."""
        token_addr = TOKEN_ADDRESSES.get(asset, asset)
        chain = NANSEN_CHAINS.get(asset, "ethereum")

        raw = await self._call_mcp_tool(
            "get_token_smart_money_flow",
            {
                "token_address": token_addr,
                "chain": chain,
                "time_range": time_range,
            },
        )

        if not raw:
            return SmartMoneyFlow(
                asset=asset,
                chain=chain,
                fetched_at=datetime.now(timezone.utc),
            )

        return SmartMoneyFlow(
            asset=asset,
            chain=chain,
            net_flow_usd=_safe_decimal(raw.get("net_flow_usd")),
            net_flow_usd_7d=_safe_decimal(raw.get("net_flow_usd_7d")),
            unique_smart_wallets=int(raw.get("unique_smart_wallets", 0)),
            flow_direction=str(raw.get("flow_direction", "NEUTRAL")),
            time_range=time_range,
            fetched_at=datetime.now(timezone.utc),
        )

    async def fetch_exchange_netflow(
        self,
        asset: str,
        time_range: str = "24h",
    ) -> ExchangeNetflow:
        """Fetch exchange netflow via MCP SSE tool call."""
        token_addr = TOKEN_ADDRESSES.get(asset, asset)
        chain = NANSEN_CHAINS.get(asset, "ethereum")

        raw = await self._call_mcp_tool(
            "get_exchange_netflow",
            {
                "token_address": token_addr,
                "chain": chain,
                "time_range": time_range,
            },
        )

        if not raw:
            return ExchangeNetflow(
                asset=asset,
                fetched_at=datetime.now(timezone.utc),
            )

        return ExchangeNetflow(
            asset=asset,
            netflow_usd=_safe_decimal(raw.get("net_flow_usd")),
            inflow_usd=_safe_decimal(raw.get("inflow_usd")),
            outflow_usd=_safe_decimal(raw.get("outflow_usd")),
            fetched_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Public API — fetch_data (BaseProvider contract)
    # ------------------------------------------------------------------

    async def fetch_data(self, asset: str) -> NansenSnapshot:
        """Assemble full NansenSnapshot via concurrent SSE calls.

        Returns the Pydantic object directly — NEVER .model_dump().
        """
        tasks = [
            self.fetch_smart_money_flow(asset, "24h"),
            self.fetch_smart_money_flow(asset, "7d"),
            self.fetch_exchange_netflow(asset, "24h"),
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        sm_24h = self._safe_result(results[0], asset, "24h")
        sm_7d = self._safe_result(results[1], asset, "7d")
        ex_flow = self._safe_exchange(results[2], asset)

        stale = self._is_stale(sm_24h, sm_7d)

        return NansenSnapshot(
            asset=asset,
            smart_money_flow_24h=sm_24h,
            smart_money_flow_7d=sm_7d,
            exchange_netflow=ex_flow,
            fetched_at=datetime.now(timezone.utc),
            stale=stale,
            status="STALE" if stale else "OK",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _safe_result(
        self,
        result: Any,
        asset: str,
        time_range: str,
    ) -> SmartMoneyFlow:
        """Unwrap gather result, returning empty model on exception."""
        if isinstance(result, BaseException):
            logger.warning(
                "nansen_gather_exception | asset={} | error={}",
                asset,
                result,
            )
            return SmartMoneyFlow(
                asset=asset,
                chain=NANSEN_CHAINS.get(asset, "ethereum"),
                fetched_at=datetime.now(timezone.utc),
            )
        return result

    def _safe_exchange(
        self,
        result: Any,
        asset: str,
    ) -> ExchangeNetflow:
        """Unwrap exchange gather result safely."""
        if isinstance(result, BaseException):
            logger.warning(
                "nansen_gather_exchange_exception | asset={} | error={}",
                asset,
                result,
            )
            return ExchangeNetflow(
                asset=asset,
                fetched_at=datetime.now(timezone.utc),
            )
        return result

    def _is_stale(
        self,
        sm_24h: SmartMoneyFlow,
        sm_7d: SmartMoneyFlow,
    ) -> bool:
        """Determine if data is stale (empty responses)."""
        return all(
            r.net_flow_usd == Decimal("0") and r.unique_smart_wallets == 0
            for r in [sm_24h, sm_7d]
        )
