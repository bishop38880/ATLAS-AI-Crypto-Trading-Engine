"""Alternative.me Fear & Greed Provider — Tier 2 macro sentiment.

Connects to the Alternative.me API using ``httpx.AsyncClient`` from the shared pool.
Uses ``msgspec.json.decode`` for all JSON parsing. Returns ``AlternativeMeSnapshot``
Pydantic objects — NEVER ``.model_dump()``.

Sentinel v3.0 invariants enforced:
- No ``import json`` — ``msgspec.json.decode`` only
- No ``import requests`` — ``httpx`` only
- Loguru structured logging (no f-strings)
- 40-line function limit
- Startup Resilience: UNAVAILABLE status on failure, returns degraded object.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.alternative_me.models import (
    AlternativeMeSnapshot,
    FearAndGreedData,
)
from atlas.shared.config import PolarisSettings


class AlternativeMeConnector(BaseProvider):
    """Tier 2 Alternative.me Fear & Greed Index — REST adapter.
    
    PRIMARY CONSUMER: SentimentAgent (macro baseline via Alternative.me F&G).
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Initialise Alternative.me provider.

        Args:
            redis_client: Shared async Redis connection.
            settings: Configuration settings.
            http_client: Injected AsyncClient from shared HTTP pool.
        """
        super().__init__("alternative_me", redis_client, max_concurrent=5)
        self._settings = settings
        self._http = http_client
        self._base_url = settings.alternative_me_base_url
        self._ttl = settings.alternative_me_ttl_seconds
        self._last_success: float = 0.0

    async def get_health_status(self) -> ProviderHealth:
        """Return immutable health snapshot."""
        return ProviderHealth(
            name=self._provider_name,
            status=self._status,
            last_update=self._last_success,
            error=self._last_error,
        )

    async def close(self) -> None:
        """Release resources — HTTP client managed externally."""
        logger.info("alternative_me_close | provider=alternative_me")

    async def fetch_data(self) -> AlternativeMeSnapshot:
        """Fetch latest Fear & Greed Index from Alternative.me.
        
        Returns the RAW Pydantic object. NEVER calls .model_dump().
        """
        # 1. Try Cache
        cached = await self._try_cached_snapshot()
        if cached:
            return cached

        # 2. Fetch Remote
        try:
            response = await self._http.get(
                f"{self._base_url}?limit=1", timeout=10.0
            )
            response.raise_for_status()
            
            # 3. Decode & Parse
            raw: dict[str, Any] = msgspec.json.decode(response.content)
            data_list = raw.get("data", [])
            if not data_list:
                return self._build_degraded("empty_data")

            # Pydantic handles string -> int casting via validator
            fng_data = FearAndGreedData(**data_list[0])
            snapshot = AlternativeMeSnapshot(data=fng_data, status="HEALTHY")
            
            # 4. Success side effects
            self._last_success = time.monotonic()
            self.mark_healthy()
            await self._persist_snapshot(snapshot)
            return snapshot

        except Exception as exc:
            logger.warning("alternative_me_fetch_fail | error={}", exc)
            self.mark_degraded(str(exc))
            return self._build_degraded(str(exc))

    async def _try_cached_snapshot(self) -> AlternativeMeSnapshot | None:
        """Attempt to retrieve from Redis cache."""
        try:
            key = "atlas:provider:alternative_me:snapshot"
            cached = await self._redis.get(key)
            if cached:
                raw: dict[str, Any] = msgspec.json.decode(cached)
                return AlternativeMeSnapshot(**raw)
        except Exception as exc:
            logger.warning("alternative_me_cache_read_fail | error={}", exc)
        return None

    async def _persist_snapshot(self, snapshot: AlternativeMeSnapshot) -> None:
        """Cache the snapshot in Redis."""
        try:
            key = "atlas:provider:alternative_me:snapshot"
            # Invariant: Use msgspec.json.encode
            # Pydantic models require model_dump() for msgspec compatibility
            encoded = msgspec.json.encode(snapshot.model_dump())
            await self._redis.setex(key, self._ttl, encoded)
        except Exception as exc:
            logger.warning("alternative_me_cache_write_fail | error={}", exc)

    def _build_degraded(self, reason: str) -> AlternativeMeSnapshot:
        """Build a safe degraded default object."""
        return AlternativeMeSnapshot(
            status="UNAVAILABLE",
            stale=True,
            error=reason
        )
