"""altFINS provider adapter for technical indicator inputs."""

import asyncio
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel

from atlas.providers.base import BaseProvider, ProviderHealth

ALTFINS_BASE_URL = "https://api.altfins.com"
ALTFINS_SIGNALS_TTL = 30
ALTFINS_SUMMARY_TTL = 30
SUPPORTED_TIMEFRAMES = ("15m", "1h", "4h", "1d", "1w")
ALTFINS_REQUEST_TIMEOUT = 10.0


class TimeframeSignal(BaseModel, frozen=True):
    """Structured technical signal for a specific timeframe."""

    timeframe: str
    trend_direction: Literal["bullish", "bearish", "neutral"]
    signal_count: int
    indicator_agreement_pct: float
    top_signals: list[str]
    raw_score: float
    status: Literal["healthy", "degraded"]


class TechnicalSignalsData(BaseModel, frozen=True):
    """Aggregate multi-timeframe signals for an asset."""

    asset: str
    timeframes: dict[str, TimeframeSignal]
    status: Literal["healthy", "degraded"]


class CoinTechnicalSummary(BaseModel, frozen=True):
    """Coin summary detailing various indicators and patterns."""

    asset: str
    overall_signal: str
    overall_score: float
    patterns_detected: list[str]
    support_levels: list[float]
    resistance_levels: list[float]
    rsi_14: float
    macd_signal: Literal["buy", "sell", "neutral"]
    volume_trend: Literal["increasing", "decreasing", "flat"]
    last_updated_utc: str
    status: Literal["healthy", "degraded"]


class AltFinsAdapter(BaseProvider):
    """Adapter for AltFINS structured technical indicators."""

    def __init__(self, redis_client: redis_async.Redis) -> None:
        """Initialize adapter strictly scoped to TechnicalAgent."""
        super().__init__("altfins", redis_client)
        self.client = httpx.AsyncClient(
            base_url=ALTFINS_BASE_URL,
            timeout=httpx.Timeout(ALTFINS_REQUEST_TIMEOUT),
        )

    async def fetch_technical_signals(self, asset: str) -> TechnicalSignalsData:
        """Fetch multi-timeframe technical signals from altFINS."""
        cache_key = f"provider:altfins:signals:{asset}"
        cached = await self._redis.get(cache_key)
        if cached:
            return TechnicalSignalsData.model_construct(
                **msgspec.json.decode(cached),
            )

        try:
            async with self._semaphore:
                res = await self.client.get(f"/api/v1/signals/{asset}")
                res.raise_for_status()

            data = TechnicalSignalsData.model_validate(res.json())
            await self._redis.setex(
                cache_key, ALTFINS_SIGNALS_TTL,
                msgspec.json.encode(data.model_dump(mode="json")),
            )
            return data
        except Exception as e:
            self.mark_degraded(f"Failed to fetch signals for {asset}: {e}")
            return self.calculate_empty_fallback_signals(asset)

    async def fetch_coin_summary(self, asset: str) -> CoinTechnicalSummary:
        """Fetch summary technical metrics from altFINS."""
        cache_key = f"provider:altfins:summary:{asset}"
        cached = await self._redis.get(cache_key)
        if cached:
            return CoinTechnicalSummary.model_construct(
                **msgspec.json.decode(cached),
            )

        try:
            async with self._semaphore:
                res = await self.client.get(f"/api/v1/summary/{asset}")
                res.raise_for_status()

            data = CoinTechnicalSummary.model_validate(res.json())
            await self._redis.setex(
                cache_key, ALTFINS_SUMMARY_TTL,
                msgspec.json.encode(data.model_dump(mode="json")),
            )
            return data
        except Exception as e:
            self.mark_degraded(f"Failed to fetch summary for {asset}: {e}")
            return self.calculate_empty_fallback_summary(asset)

    def calculate_empty_fallback_signals(self, asset: str) -> TechnicalSignalsData:
        """Return a deterministic empty signals fallback."""
        timeframes = {
            tf: TimeframeSignal(
                timeframe=tf,
                trend_direction="neutral",
                signal_count=0,
                indicator_agreement_pct=0.0,
                top_signals=[],
                raw_score=0.0,
                status="degraded",
            )
            for tf in SUPPORTED_TIMEFRAMES
        }
        return TechnicalSignalsData(asset=asset, timeframes=timeframes, status="degraded")

    def calculate_empty_fallback_summary(self, asset: str) -> CoinTechnicalSummary:
        """Return a deterministic empty summary fallback."""
        return CoinTechnicalSummary(
            asset=asset,
            overall_signal="neutral",
            overall_score=0.0,
            patterns_detected=[],
            support_levels=[],
            resistance_levels=[],
            rsi_14=0.0,
            macd_signal="neutral",
            volume_trend="flat",
            last_updated_utc=datetime.now(timezone.utc).isoformat(),
            status="degraded",
        )

    async def get_health_status(self) -> ProviderHealth:
        """Ping the altFINS API to determine health status."""
        try:
            async with self._semaphore:
                res = await self.client.get("/ping")
                res.raise_for_status()
            self.mark_healthy()
        except Exception as e:
            self.mark_degraded(f"Health ping failed: {e}")

        return ProviderHealth(
            name=self.provider_name,
            status=self.status,
            last_update=time.monotonic(),
            error=self._last_error,
        )

    async def close(self) -> None:
        """Release HTTP client resources."""
        await self.client.aclose()
