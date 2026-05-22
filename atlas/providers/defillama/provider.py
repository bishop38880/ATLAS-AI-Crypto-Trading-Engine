"""DeFi Llama standalone provider adapter.

NO BaseProvider inheritance - degraded on failure, system continues.
Trust Rank: #7.
TTL: 300s (TVL/stablecoins), 600s (yield pools).
"""

from typing import Any

import redis.asyncio as redis
from loguru import logger

from atlas.providers.defillama.cache import (
    read_chain_tvl,
    read_protocol_tvls,
    read_stablecoin_supply,
    read_yield_pools,
    write_chain_tvl,
    write_health,
    write_protocol_tvls,
    write_stablecoin_supply,
    write_yield_pools,
)
from atlas.providers.defillama.connector import DefiLlamaMCPConnector
from atlas.providers.defillama.models import DefiLlamaContext
from atlas.shared.config import PolarisSettings

_MONITORED_CHAINS = ["Ethereum", "Solana", "BSC", "Arbitrum", "Base"]


class DefiLlamaProvider:
    """Standalone DeFi Llama MCP adapter. Tier 2."""

    def __init__(self, r: redis.Redis, settings: PolarisSettings) -> None:
        self._redis = r
        self._settings = settings
        self._connector = DefiLlamaMCPConnector(settings)

    def get_tier(self) -> int:
        return 2

    def get_cache_ttl(self) -> int:
        return self._settings.defillama_ttl_tvl_seconds

    async def get_health_status(self) -> dict[str, Any]:
        """Get component health status and cache it."""
        health = self._connector.get_health()
        await write_health(self._redis, health)
        return health

    async def _fetch_chain_tvl(self, chain: str) -> Any:
        cached = await read_chain_tvl(self._redis, chain)
        if cached:
            return cached

        data = await self._connector.fetch_chain_tvl(chain)
        if data:
            await write_chain_tvl(
                self._redis, chain, data, self._settings.defillama_ttl_tvl_seconds
            )
        return data

    async def _fetch_protocol_tvls(self) -> Any:
        cached = await read_protocol_tvls(self._redis)
        if cached:
            return cached

        data = await self._connector.fetch_protocol_tvls()
        if data is not None:
            await write_protocol_tvls(
                self._redis, data, self._settings.defillama_ttl_tvl_seconds
            )
        return data

    async def _fetch_stablecoin_supply(self) -> Any:
        cached = await read_stablecoin_supply(self._redis)
        if cached:
            return cached

        data = await self._connector.fetch_stablecoin_supply()
        if data:
            await write_stablecoin_supply(
                self._redis, data, self._settings.defillama_ttl_stablecoin_seconds
            )
        return data

    async def _fetch_yield_pools(self) -> Any:
        cached = await read_yield_pools(self._redis)
        if cached:
            return cached

        data = await self._connector.fetch_yield_pools()
        if data is not None:
            await write_yield_pools(self._redis, data, 600)
        return data

    async def _safe_fetch(self, coro: Any) -> Any:
        try:
            return await coro
        except Exception as e:
            logger.warning("defillama fetch failed | error={}", str(e))
            return None

    async def fetch_data(self, chain: str = "Ethereum") -> DefiLlamaContext:
        """Fetch aggregated DeFi Llama context. Cache-first with degraded fallback."""
        import asyncio
        
        chain_tvl, protocol_tvls, stablecoin, yield_pools = await asyncio.gather(
            self._safe_fetch(self._fetch_chain_tvl(chain)),
            self._safe_fetch(self._fetch_protocol_tvls()),
            self._safe_fetch(self._fetch_stablecoin_supply()),
            self._safe_fetch(self._fetch_yield_pools())
        )

        if chain_tvl is None and stablecoin is None:
            logger.warning("defillama: all primary fetches failed for {}", chain)

        return DefiLlamaContext(
            chain_tvl=chain_tvl,
            protocol_tvls=protocol_tvls or [],
            stablecoin_supply=stablecoin,
            yield_pools=yield_pools or [],
        )
