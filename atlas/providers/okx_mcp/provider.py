"""OKX MCP provider — BaseProvider implementation.

Cache-first fetch pattern. Health status tracking.
"""

import time

import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.okx_mcp import cache
from atlas.providers.okx_mcp.connector import OKXMCPConnector, normalise_to_okx_instid
from atlas.providers.okx_mcp.models import (
    OKXFundingHistory,
    OKXFundingRate,
    OKXLiquidationSnapshot,
    OKXLongShortRatio,
    OKXOpenInterest,
    OKXOpenInterestHistory,
)


class OKXMCPProvider(BaseProvider):
    """OKX MCP data provider — primary derivatives feed."""

    def __init__(
        self,
        redis_client: redis_async.Redis,
        mcp_url: str = "https://mcp.okx.com",
    ) -> None:
        """Initialise OKX MCP provider.

        Args:
            redis_client: Shared async Redis connection.
            mcp_url: OKX MCP server URL.
        """
        super().__init__("okx_mcp", redis_client, max_concurrent=10)
        self._connector = OKXMCPConnector(mcp_url)

    async def get_health_status(self) -> ProviderHealth:
        """Return immutable health snapshot."""
        health = self._connector.get_health()
        await cache.write_health(self._redis, health)
        return ProviderHealth(
            name="okx_mcp",
            status=self._status,
            last_update=time.monotonic(),
            error=self._last_error,
        )

    async def close(self) -> None:
        """Release resources."""
        logger.info("okx_mcp provider closed")

    async def fetch_funding_rate(
        self, asset: str,
    ) -> OKXFundingRate | None:
        """Fetch funding rate — cache first."""
        cached = await cache.read_funding_rate(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        data = await self._connector.fetch_funding_rate(inst_id)
        if data:
            await cache.write_funding_rate(self._redis, asset, data)
            self.mark_healthy()
        return data

    async def fetch_funding_history(
        self, asset: str, limit: int = 90,
    ) -> OKXFundingHistory | None:
        """Fetch funding history — cache first."""
        cached = await cache.read_funding_history(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        data = await self._connector.fetch_funding_history(inst_id, limit)
        if data:
            await cache.write_funding_history(self._redis, asset, data)
            self.mark_healthy()
        return data

    async def fetch_open_interest(
        self, asset: str,
    ) -> OKXOpenInterest | None:
        """Fetch OI — cache first."""
        cached = await cache.read_open_interest(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        data = await self._connector.fetch_open_interest(inst_id)
        if data:
            await cache.write_open_interest(self._redis, asset, data)
            self.mark_healthy()
        return data

    async def fetch_oi_history(
        self, asset: str, limit: int = 336,
    ) -> OKXOpenInterestHistory | None:
        """Fetch OI history — cache first."""
        cached = await cache.read_oi_history(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        data = await self._connector.fetch_oi_history(inst_id, limit)
        if data:
            await cache.write_oi_history(self._redis, asset, data)
            self.mark_healthy()
        return data

    async def fetch_long_short_ratio(
        self, asset: str,
    ) -> OKXLongShortRatio | None:
        """Fetch L/S ratio — cache first."""
        cached = await cache.read_long_short_ratio(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        data = await self._connector.fetch_long_short_ratio(inst_id)
        if data:
            await cache.write_long_short_ratio(self._redis, asset, data)
            self.mark_healthy()
        return data

    async def fetch_liquidations(
        self, asset: str,
    ) -> OKXLiquidationSnapshot | None:
        """Fetch liquidations — cache first."""
        cached = await cache.read_liquidations(self._redis, asset)
        if cached:
            return cached
        inst_id = normalise_to_okx_instid(asset)
        uly = inst_id.replace("-SWAP", "")
        data = await self._connector.fetch_liquidations(inst_id, uly)
        if data:
            await cache.write_liquidations(self._redis, asset, data)
            self.mark_healthy()
        return data
