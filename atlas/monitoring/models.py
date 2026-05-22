"""Pydantic models for hourly market monitoring pipeline."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class ProviderFetchMeta(BaseModel, frozen=True):
    """Metadata for a single provider attempt."""

    provider: str
    key_id: str
    latency_ms: float
    status: Literal["ok", "degraded", "cached", "failed"]
    http_status: int | None = None
    error: str | None = None


class NormalizedHourlyQuote(BaseModel, frozen=True):
    """Unified hourly quote used for persistence and analytics."""

    asset_base: str = Field(description="POLARIS base symbol, e.g. BTC")
    sampled_at: datetime
    price_usd: Decimal
    volume_24h_usd: Decimal | None = None
    market_cap_usd: Decimal | None = None
    source_provider: str
    source_key_id: str
    cross_check_price_usd: Decimal | None = None
    cross_check_provider: str | None = None
    quality: Literal["full", "partial", "degraded"] = "full"
    degraded_reasons: tuple[str, ...] = Field(default_factory=tuple)


class AssetHourlyAnalytics(BaseModel, frozen=True):
    """Per-asset analytics for one hourly bucket."""

    asset_base: str
    sampled_at: datetime
    hourly_return_pct: float | None = None
    rolling_volatility_pct: float | None = None
    momentum_pct: float | None = None
    volume_z_score: float | None = None
    market_cap_rank: int | None = None
    rank_change: int | None = None
    relative_strength: float | None = None


class BasketHourlyAnalytics(BaseModel, frozen=True):
    """Cross-sectional basket metrics for the active 8."""

    sampled_at: datetime
    breadth_positive_pct: float
    median_hourly_return_pct: float | None = None
    benchmark_asset: str = "BTC"


class HourlyMonitorCycleResult(BaseModel, frozen=True):
    """Full cycle output exposed to Redis / API."""

    sampled_at: datetime
    assets: tuple[str, ...]
    quotes: tuple[NormalizedHourlyQuote, ...]
    per_asset_analytics: tuple[AssetHourlyAnalytics, ...]
    basket: BasketHourlyAnalytics
    fetch_meta: tuple[ProviderFetchMeta, ...]
    alert_count: int = 0
