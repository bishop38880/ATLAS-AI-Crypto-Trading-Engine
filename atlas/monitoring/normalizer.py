"""Merge multi-provider fetches into a single normalized hourly quote."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from atlas.monitoring.models import NormalizedHourlyQuote


def normalize_hourly_quote(
    *,
    asset_base: str,
    sampled_at: datetime,
    primary_price_usd: Decimal,
    primary_provider: str,
    primary_key_id: str,
    volume_24h_usd: Decimal | None,
    market_cap_usd: Decimal | None,
    cross_price_usd: Decimal | None,
    cross_provider: str | None,
    divergence_threshold_pct: float,
) -> NormalizedHourlyQuote:
    """Build a unified quote; mark degraded when cross-source divergence is high."""
    reasons: list[str] = []
    quality: str = "full"

    if primary_price_usd <= 0:
        quality = "degraded"
        reasons.append("primary_price_missing")

    if cross_price_usd is not None and cross_price_usd > 0 and primary_price_usd > 0:
        divergence = abs(primary_price_usd - cross_price_usd) / primary_price_usd * Decimal("100")
        if float(divergence) > divergence_threshold_pct:
            quality = "partial"
            reasons.append("cross_source_divergence")

    if volume_24h_usd is None:
        quality = "partial" if quality == "full" else quality
        reasons.append("volume_missing")

    return NormalizedHourlyQuote(
        asset_base=asset_base.upper(),
        sampled_at=sampled_at.astimezone(timezone.utc),
        price_usd=primary_price_usd,
        volume_24h_usd=volume_24h_usd,
        market_cap_usd=market_cap_usd,
        source_provider=primary_provider,
        source_key_id=primary_key_id,
        cross_check_price_usd=cross_price_usd,
        cross_check_provider=cross_provider,
        quality=quality,  # type: ignore[arg-type]
        degraded_reasons=tuple(reasons),
    )
