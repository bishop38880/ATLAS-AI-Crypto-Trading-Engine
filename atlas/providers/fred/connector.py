"""FREDProvider — Tier 2 macroeconomic data via REST.

Connects to the FRED API using ``httpx.AsyncClient`` from the shared pool.
Uses ``msgspec.json.decode`` for all JSON parsing. Returns ``MacroSnapshot``
Pydantic objects — NEVER ``.model_dump()``.

Sentinel v3.0 invariants enforced:
  - Decimal for all yields, rates, and supply metrics
  - No ``import json`` — ``msgspec.json.decode`` only
  - No ``import requests`` — ``httpx`` only
  - No ``raise ValueError`` on missing API key — graceful degradation
  - Loguru structured logging (no f-strings)
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.base import BaseProvider, ProviderHealth
from atlas.providers.fred.models import MacroSnapshot, YieldCurveData
from atlas.shared.config import PolarisSettings


def _safe_decimal(val: Any) -> Decimal:
    """Cast a raw value to Decimal safely."""
    if val is None or val == ".":
        return Decimal("0")
    try:
        return Decimal(str(val))
    except (InvalidOperation, ValueError):
        return Decimal("0")


class FREDProvider(BaseProvider):
    """Tier 2 FRED macroeconomic data — REST adapter.

    PRIMARY CONSUMER: NewsMacroAgent (yield curve, fed funds, CPI).
    """

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Initialise FRED provider.

        Args:
            redis_client: Shared async Redis connection.
            settings: Configuration settings.
            http_client: Injected AsyncClient from shared HTTP pool.
        """
        super().__init__("fred_rest", redis_client, max_concurrent=5)
        self._settings = settings
        self._http = http_client
        self._base_url = settings.fred_base_url
        self._ttl = settings.fred_ttl_seconds
        self._last_success: float = 0.0

        api_key = settings.fred_api_key.get_secret_value()
        self._api_key: str = api_key if api_key else ""

        if not self._api_key:
            logger.warning(
                "fred_provider_init | status=OFFLINE | reason=missing_api_key"
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
        """Release resources — HTTP client lifecycle managed externally."""
        logger.info("fred_provider_close | provider=fred_rest")

    # ------------------------------------------------------------------
    # Series fetch
    # ------------------------------------------------------------------

    def _build_series_params(self, series_id: str) -> tuple[str, dict[str, Any]]:
        """Construct URL and query params for a FRED series request."""
        url = f"{self._base_url}/fred/series/observations"
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self._api_key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 1,
        }
        return url, params

    async def _fetch_series(self, series_id: str) -> dict[str, Any]:
        """Fetch latest observation for a FRED series."""
        if self._status == "OFFLINE":
            return {}

        url, params = self._build_series_params(series_id)

        async with self._semaphore:
            try:
                response = await self._http.get(
                    url, params=params, timeout=10.0,
                )
                response.raise_for_status()
                data: dict[str, Any] = msgspec.json.decode(response.content)
                observations = data.get("observations", [])
                if observations and len(observations) > 0:
                    self._last_success = time.monotonic()
                    self.mark_healthy()
                    return observations[0]
                return {}
            except httpx.TimeoutException:
                logger.warning("fred_timeout | series={}", series_id)
                self.mark_degraded("timeout on {}".format(series_id))
                return {}
            except Exception as exc:
                logger.warning(
                    "fred_fetch_error | series={} | error={}",
                    series_id, exc,
                )
                self.mark_degraded(str(exc))
                return {}

    # ------------------------------------------------------------------
    # Snapshot assembly
    # ------------------------------------------------------------------

    async def fetch_macro_snapshot(self) -> MacroSnapshot:
        """Fetch aggregated macro snapshot from FRED.

        Returns the Pydantic object directly — NEVER .model_dump().
        """
        if self._status == "OFFLINE":
            return MacroSnapshot(stale=True, status="degraded")

        cached = await self._try_cached_snapshot()
        if cached is not None:
            return cached

        results = await self._fetch_all_series()
        snapshot = self._parse_series_results(results)
        await self._persist_snapshot(snapshot)
        return snapshot

    async def _try_cached_snapshot(self) -> MacroSnapshot | None:
        """Attempt to return a cached snapshot from Redis."""
        try:
            async with asyncio.timeout(2.0):
                cached = await self._redis.get("atlas:provider:fred:macro_snapshot")
            if cached:
                raw: dict[str, Any] = msgspec.json.decode(cached)
                return MacroSnapshot(**raw)
        except Exception as exc:
            logger.warning("fred_cache_decode_error | error={}", exc)
        return None

    async def _fetch_all_series(self) -> list[dict[str, Any]]:
        """Fetch all FRED series in parallel."""
        results = await asyncio.gather(
            self._fetch_series("FEDFUNDS"),
            self._fetch_series("DGS10"),
            self._fetch_series("DGS2"),
            self._fetch_series("CPIAUCSL"),
        )
        return list(results)

    def _parse_series_results(
        self, results: list[dict[str, Any]],
    ) -> MacroSnapshot:
        """Parse raw FRED results into a MacroSnapshot."""
        is_stale = False

        ff_rate, is_stale = self._extract_decimal(results[0], is_stale)
        ten_year_str, is_stale = self._extract_str(results[1], is_stale)
        two_year_str, is_stale = self._extract_str(results[2], is_stale)
        cpi_val, is_stale = self._extract_decimal(results[3], is_stale)

        yc = self._build_yield_curve(ten_year_str, two_year_str)
        if yc is None:
            is_stale = True
            yc = YieldCurveData()

        return MacroSnapshot(
            fed_funds_rate=ff_rate,
            cpi_yoy=cpi_val,
            yield_curve=yc,
            stale=is_stale,
            status="degraded" if is_stale else "healthy",
        )

    @staticmethod
    def _extract_decimal(
        obs: dict[str, Any],
        stale: bool,
    ) -> tuple[Decimal, bool]:
        """Extract a Decimal value from an observation dict."""
        if isinstance(obs, dict) and obs.get("value") and obs["value"] != ".":
            return _safe_decimal(obs["value"]), stale
        return Decimal("0"), True

    @staticmethod
    def _extract_str(
        obs: dict[str, Any],
        stale: bool,
    ) -> tuple[str, bool]:
        """Extract a string value from an observation dict."""
        if isinstance(obs, dict) and obs.get("value") and obs["value"] != ".":
            return str(obs["value"]), stale
        return "0", True

    @staticmethod
    def _build_yield_curve(
        ten_year: str, two_year: str,
    ) -> YieldCurveData | None:
        """Build YieldCurveData from string values."""
        try:
            ty = Decimal(ten_year)
            tw = Decimal(two_year)
            return YieldCurveData(ten_year=ty, two_year=tw, spread=ty - tw)
        except (InvalidOperation, ValueError) as exc:
            logger.warning("fred_yield_curve_error | error={}", exc)
            return None

    async def _persist_snapshot(self, snapshot: MacroSnapshot) -> None:
        """Cache snapshot in Redis if not stale."""
        if snapshot.stale:
            return
        try:
            encoded = msgspec.json.encode(snapshot.model_dump(mode="json"))
            async with asyncio.timeout(2.0):
                await self._redis.setex(
                    "atlas:provider:fred:macro_snapshot",
                    self._ttl,
                    encoded,
                )
        except Exception as exc:
            logger.warning("fred_cache_write_error | error={}", exc)

    # ------------------------------------------------------------------
    # Public API — fetch_data (BaseProvider contract)
    # ------------------------------------------------------------------

    async def fetch_data(self) -> MacroSnapshot:
        """Alias for fetch_macro_snapshot.

        NOTE: FRED is global macro data — no asset parameter needed.
        Returns the Pydantic object directly — NEVER .model_dump().
        """
        return await self.fetch_macro_snapshot()
