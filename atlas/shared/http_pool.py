"""HTTP connection pooling primitives using httpx."""

from __future__ import annotations

from decimal import Decimal
import time
from typing import Any
import httpx
import redis.asyncio as redis_async
from loguru import logger

from atlas.core.provider_health import ProviderHealthTracker


class ProviderHttpPool:
    """Connection pool manager for data providers.
    
    Creates a single `httpx.AsyncClient` to reuse connections
    across the entire ATLAS stack. Supports HTTP/2.
    """

    def __init__(
        self,
        provider_name: str,
        base_url: str,
        redis_client: redis_async.Redis,
        max_connections: int = 20,
        max_keepalive: int = 10,
    ) -> None:
        """Initialize the connection pool."""
        self.provider_name = provider_name
        self._health = ProviderHealthTracker(redis_client=redis_client)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            http2=True,
            timeout=httpx.Timeout(2.0, connect=1.0),
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=max_keepalive,
            ),
        )

    async def health_check(self) -> bool:
        """Ping the base URL to verify connectivity."""
        try:
            resp = await self._client.get("/", timeout=httpx.Timeout(2.0))
            return resp.status_code < 500
        except Exception as exc:
            logger.error("pool_health_check_failed | provider={} | err={}", self.provider_name, exc)
            return False

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Execute a GET request using the connection pool."""
        timeout = await self._health.get_timeout(self.provider_name)
        start_time = time.perf_counter()
        success = False
        try:
            # Overriding default timeout dynamically
            resp = await self._client.get(path, params=params, timeout=timeout)
            resp.raise_for_status()
            success = True
            return resp
        finally:
            latency = time.perf_counter() - start_time
            await self._health.record_request(self.provider_name, success, latency)

    async def close(self) -> None:
        """Gracefully close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> ProviderHttpPool:
        """Support for async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any | None,
    ) -> None:
        """Close the pool on context exit."""
        await self.close()
