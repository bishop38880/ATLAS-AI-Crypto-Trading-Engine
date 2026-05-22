"""Deribit public REST provider — BTC/ETH options chain and intelligence."""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.deribit.chain_builder import (
    build_chain_snapshot_from_book_summaries,
    build_expiry_iv_pairs,
)
from atlas.providers.deribit.intelligence import calculate_options_intelligence
from atlas.providers.deribit.models import OptionsChainSnapshot, OptionsIntelligence
from atlas.shared.config import PolarisSettings

DERIBIT_PUBLIC_BASE_URL = "https://www.deribit.com/api/v2/public"
PROVIDER_TIER = 2
TRUST_RANK = 7
CACHE_TTL_SECONDS = 300
_SUPPORTED_ASSETS = frozenset({"BTC", "ETH"})


class DeribitOptionsProvider(BaseProvider):
    """Tier-2 Deribit options market data — no API key required."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        super().__init__("deribit_options", redis_client, max_concurrent=5)
        self._settings = settings
        self._http = http_client
        self._base_url = settings.deribit_options_base_url
        self._ttl = settings.deribit_options_ttl_seconds
        self._timeout = settings.deribit_options_timeout_seconds
        self._last_success: float = 0.0

    @staticmethod
    def get_tier() -> int:
        return PROVIDER_TIER

    @staticmethod
    def get_trust_rank() -> int:
        return TRUST_RANK

    async def get_health_status(self) -> ProviderHealth:
        return ProviderHealth(
            name=self._provider_name,
            status=self._status,
            last_update=self._last_success,
            error=self._last_error,
        )

    async def close(self) -> None:
        logger.info("deribit_options_close | provider=deribit_options")

    async def fetch_options_chain(self, asset: str) -> OptionsChainSnapshot:
        """Fetch and aggregate the options chain for BTC or ETH."""
        normalized = asset.upper().replace("USDT", "").replace("/", "")
        if normalized not in _SUPPORTED_ASSETS:
            return self._empty_chain_snapshot(normalized)

        cached = await self._read_cached_chain(normalized)
        if cached is not None:
            return cached

        try:
            summaries = await self._fetch_book_summaries(normalized)
            snapshot = build_chain_snapshot_from_book_summaries(normalized, summaries)
            self._last_success = time.monotonic()
            self.mark_healthy()
            await self._persist_chain_cache(normalized, snapshot)
            return snapshot
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("deribit_chain_fetch_fail | asset={} | error={}", normalized, exc)
            self.mark_degraded(str(exc))
            return self._empty_chain_snapshot(normalized)

    async def compute_intelligence(
        self,
        snapshot: OptionsChainSnapshot,
    ) -> OptionsIntelligence:
        """CPU-bound intelligence derivation — runs in a worker thread."""
        summaries_key = f"atlas:provider:deribit_options:{snapshot.asset}:raw"
        raw_summaries = await self._redis.get(summaries_key)
        expiry_pairs: list[tuple[int, Decimal]] = []
        if raw_summaries:
            decoded: list[dict[str, Any]] = msgspec.json.decode(raw_summaries)
            expiry_pairs = build_expiry_iv_pairs(decoded, snapshot.spot_price)

        return await asyncio.to_thread(
            calculate_options_intelligence,
            snapshot,
            expiry_pairs if expiry_pairs else None,
        )

    async def fetch_intelligence(self, asset: str) -> OptionsIntelligence:
        """Fetch chain and return computed intelligence (cached)."""
        normalized = asset.upper().replace("USDT", "").replace("/", "")
        cache_key = f"atlas:provider:deribit_options:{normalized}:intelligence"
        cached = await self._read_cached_intelligence(cache_key)
        if cached is not None:
            return cached

        chain = await self.fetch_options_chain(normalized)
        intelligence = await self.compute_intelligence(chain)
        await self._persist_intelligence_cache(cache_key, intelligence)
        return intelligence

    async def health_check(self) -> bool:
        """Ping Deribit public get_time endpoint."""
        try:
            response = await asyncio.wait_for(
                self._http.get(f"{self._base_url}/get_time"),
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = msgspec.json.decode(response.content)
            return "result" in payload
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("deribit_health_fail | error={}", exc)
            return False

    async def _fetch_book_summaries(self, currency: str) -> list[dict[str, Any]]:
        url = f"{self._base_url}/get_book_summary_by_currency"
        response = await asyncio.wait_for(
            self._http.get(url, params={"currency": currency, "kind": "option"}),
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = msgspec.json.decode(response.content)
        result = payload.get("result", [])
        if not isinstance(result, list):
            return []
        await self._persist_raw_summaries(currency, result)
        return result

    async def _persist_raw_summaries(
        self,
        currency: str,
        summaries: list[dict[str, Any]],
    ) -> None:
        key = f"atlas:provider:deribit_options:{currency}:raw"
        try:
            encoded = msgspec.json.encode(summaries)
            await self._redis.setex(key, self._ttl, encoded)
        except Exception as exc:
            logger.warning("deribit_raw_cache_fail | error={}", exc)

    async def _read_cached_chain(self, asset: str) -> OptionsChainSnapshot | None:
        key = f"atlas:provider:deribit_options:{asset}:chain"
        try:
            cached = await self._redis.get(key)
            if cached:
                raw: dict[str, Any] = msgspec.json.decode(cached)
                return OptionsChainSnapshot(**raw)
        except Exception as exc:
            logger.warning("deribit_chain_cache_read_fail | error={}", exc)
        return None

    async def _persist_chain_cache(
        self,
        asset: str,
        snapshot: OptionsChainSnapshot,
    ) -> None:
        key = f"atlas:provider:deribit_options:{asset}:chain"
        try:
            payload = snapshot.model_dump(mode="json")
            await self._redis.setex(key, self._ttl, msgspec.json.encode(payload))
        except Exception as exc:
            logger.warning("deribit_chain_cache_write_fail | error={}", exc)

    async def _read_cached_intelligence(
        self,
        cache_key: str,
    ) -> OptionsIntelligence | None:
        try:
            cached = await self._redis.get(cache_key)
            if cached:
                raw: dict[str, Any] = msgspec.json.decode(cached)
                return OptionsIntelligence(**raw)
        except Exception as exc:
            logger.warning("deribit_intel_cache_read_fail | error={}", exc)
        return None

    async def _persist_intelligence_cache(
        self,
        cache_key: str,
        intelligence: OptionsIntelligence,
    ) -> None:
        try:
            payload = intelligence.model_dump(mode="json")
            await self._redis.setex(cache_key, self._ttl, msgspec.json.encode(payload))
        except Exception as exc:
            logger.warning("deribit_intel_cache_write_fail | error={}", exc)

    def _empty_chain_snapshot(self, asset: str) -> OptionsChainSnapshot:
        from atlas.providers.deribit.intelligence import utc_now_iso

        return OptionsChainSnapshot(
            asset=asset,
            expiry="NONE",
            strikes=[],
            call_oi=[],
            put_oi=[],
            call_volume_24h=[],
            put_volume_24h=[],
            iv_call=[],
            iv_put=[],
            spot_price=Decimal("0"),
            timestamp_utc=utc_now_iso(),
        )
