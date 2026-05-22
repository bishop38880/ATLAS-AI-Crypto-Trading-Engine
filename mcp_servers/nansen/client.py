"""Hardened Nansen API client — includes Credit Governor and Rate Limiting.

Adheres to Sentinel v3.0 architectural invariants.
Uses httpx.AsyncClient for high-concurrency tool execution.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import httpx
from loguru import logger

from mcp_servers.multichain_rpc.utils.rate_limiter import RedisSlidingWindowLimiter


class CreditGovernor:
    """Tracks and enforces Nansen credit budget via Redis."""

    def __init__(
        self,
        redis_url: str,
        monthly_budget: int = 100000,
        daily_budget: int = 5000,
    ) -> None:
        self._redis_url = redis_url
        self._monthly_budget = monthly_budget
        self._daily_budget = daily_budget
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis_async
            self._client = redis_async.from_url(self._redis_url)
        return self._client

    async def check_budget(self, estimated_cost: int) -> bool:
        """Verify if the estimated cost fits within the remaining budget."""
        client = await self._get_client()
        now = datetime.now(timezone.utc)
        day_key = f"nansen:usage:day:{now.strftime('%Y-%m-%d')}"
        month_key = f"nansen:usage:month:{now.strftime('%Y-%m')}"

        # Increment and check
        try:
            day_usage = await client.incrby(day_key, estimated_cost)
            month_usage = await client.incrby(month_key, estimated_cost)
            
            if day_usage > self._daily_budget or month_usage > self._monthly_budget:
                logger.warning("Nansen credit budget exceeded | day={}/{} | month={}/{}", 
                               day_usage, self._daily_budget, month_usage, self._monthly_budget)
                # Rollback the increment if we want to be strict, but for now we just flag it.
                return False
            return True
        except Exception as e:
            logger.error("Credit governor error | err={}", e)
            return True  # Fail-open to avoid breaking the engine, but log the error

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()


class NansenClient:
    """Hardened Nansen REST API client."""

    BASE_URL = "https://api.nansen.ai/v1"

    def __init__(
        self,
        api_key: str,
        redis_url: str,
        governor: CreditGovernor,
    ) -> None:
        self._api_key = api_key
        self._governor = governor
        self._limiter = RedisSlidingWindowLimiter(
            redis_url=redis_url,
            key_prefix="ratelimit:nansen",
            max_requests=2,  # Nansen is strict
        )
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"X-API-KEY": api_key},
        )

    async def _fetch(self, endpoint: str, params: dict[str, Any], cost: int) -> dict[str, Any] | None:
        """Internal fetch with credit and rate limit enforcement."""
        if not await self._governor.check_budget(cost):
            return None

        await self._limiter.acquire()
        try:
            url = f"{self.BASE_URL}{endpoint}"
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("Nansen API request failed | endpoint={} | err={}", endpoint, e)
            return None

    async def get_token_smart_money_flow(self, token_address: str, chain: str, time_range: str) -> dict[str, Any] | None:
        """Fetch smart money flows (Cost: 10 credits)."""
        return await self._fetch("/tokens/smart-money-flow", {
            "address": token_address,
            "chain": chain,
            "range": time_range,
        }, cost=10)

    async def get_exchange_netflow(self, token_address: str, chain: str, time_range: str) -> dict[str, Any] | None:
        """Fetch exchange netflows (Cost: 5 credits)."""
        return await self._fetch("/tokens/exchange-flow", {
            "address": token_address,
            "chain": chain,
            "range": time_range,
        }, cost=5)

    async def get_smart_money_top_holders(self, token_address: str, chain: str) -> dict[str, Any] | None:
        """Fetch top smart money holders (Cost: 20 credits)."""
        return await self._fetch("/tokens/smart-money-holders", {
            "address": token_address,
            "chain": chain,
        }, cost=20)

    async def get_wallet_labels(self, wallet_address: str) -> dict[str, Any] | None:
        """Fetch labels for a specific wallet (Cost: 2 credits)."""
        return await self._fetch("/wallets/labels", {
            "address": wallet_address,
        }, cost=2)

    async def get_wallet_portfolio(self, wallet_address: str) -> dict[str, Any] | None:
        """Fetch portfolio for a specific wallet (Cost: 50 credits)."""
        return await self._fetch("/wallets/portfolio", {
            "address": wallet_address,
        }, cost=50)

    async def close(self) -> None:
        await self._client.aclose()
        await self._limiter.close()
