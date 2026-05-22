"""Provider routing with failover and last-known-good fallback."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal

from loguru import logger

from atlas.monitoring.last_known_good import read_last_known_good, write_last_known_good
from atlas.monitoring.models import NormalizedHourlyQuote, ProviderFetchMeta
from atlas.monitoring.normalizer import normalize_hourly_quote
from atlas.monitoring.sources.coingecko import CoinGeckoHourlySource
from atlas.monitoring.sources.pyth import PythCachedHourlySource
from atlas.shared.config import PolarisSettings


class HourlyProviderRouter:
    """Primary CoinGecko + Pyth cross-check with LKG cache on failure."""

    def __init__(
        self,
        settings: PolarisSettings,
        coingecko: CoinGeckoHourlySource,
        pyth: PythCachedHourlySource,
        redis_client: object,
    ) -> None:
        self._settings = settings
        self._coingecko = coingecko
        self._pyth = pyth
        self._redis = redis_client
        self._fetch_meta: list[ProviderFetchMeta] = []

    @property
    def fetch_meta(self) -> tuple[ProviderFetchMeta, ...]:
        return tuple(self._fetch_meta)

    async def fetch_normalized_quote(
        self,
        asset_base: str,
        sampled_at: datetime | None = None,
    ) -> NormalizedHourlyQuote:
        """Fetch, normalize, and cache one asset quote."""
        self._fetch_meta = []
        bucket = sampled_at or datetime.now(timezone.utc)
        primary_price = Decimal("0")
        volume: Decimal | None = None
        market_cap: Decimal | None = None
        key_id = "none"
        cross_price: Decimal | None = None

        try:
            price, volume, market_cap, key_id, latency_ms, status = await self._coingecko.fetch_spot(
                asset_base
            )
            primary_price = price
            self._fetch_meta.append(
                ProviderFetchMeta(
                    provider="coingecko",
                    key_id=key_id,
                    latency_ms=latency_ms,
                    status=status,  # type: ignore[arg-type]
                )
            )
        except Exception as exc:
            logger.warning("monitoring_primary_failed | asset={} | err={}", asset_base, exc)
            self._fetch_meta.append(
                ProviderFetchMeta(
                    provider="coingecko",
                    key_id=key_id,
                    latency_ms=0.0,
                    status="failed",
                    error=str(exc),
                )
            )

        pyth_price, pyth_latency, pyth_status = await self._pyth.fetch_spot(asset_base)
        cross_price = pyth_price
        self._fetch_meta.append(
            ProviderFetchMeta(
                provider="pyth",
                key_id="redis_cache",
                latency_ms=pyth_latency,
                status=pyth_status,  # type: ignore[arg-type]
            )
        )

        if primary_price <= 0:
            cached = await read_last_known_good(self._redis, asset_base)  # type: ignore[arg-type]
            if cached is not None:
                logger.info("monitoring_lkg_failover | asset={}", asset_base)
                return cached.model_copy(
                    update={
                        "sampled_at": bucket,
                        "quality": "degraded",
                        "degraded_reasons": ("last_known_good",),
                    }
                )

        quote = normalize_hourly_quote(
            asset_base=asset_base,
            sampled_at=bucket,
            primary_price_usd=primary_price,
            primary_provider="coingecko",
            primary_key_id=key_id,
            volume_24h_usd=volume,
            market_cap_usd=market_cap,
            cross_price_usd=cross_price,
            cross_provider="pyth" if cross_price is not None else None,
            divergence_threshold_pct=self._settings.hourly_monitor_price_divergence_pct,
        )

        if quote.quality != "degraded" and quote.price_usd > 0:
            await write_last_known_good(
                self._redis,  # type: ignore[arg-type]
                quote,
                self._settings.hourly_monitor_lkg_ttl_seconds,
            )
        return quote
