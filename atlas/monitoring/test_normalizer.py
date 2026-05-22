"""Tests for quote normalization."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from atlas.monitoring.normalizer import normalize_hourly_quote


class TestNormalizeHourlyQuote:
    def test_full_quality_when_aligned(self) -> None:
        quote = normalize_hourly_quote(
            asset_base="btc",
            sampled_at=datetime.now(timezone.utc),
            primary_price_usd=Decimal("100"),
            primary_provider="coingecko",
            primary_key_id="primary",
            volume_24h_usd=Decimal("1e9"),
            market_cap_usd=Decimal("1e12"),
            cross_price_usd=Decimal("100.1"),
            cross_provider="pyth",
            divergence_threshold_pct=0.5,
        )
        assert quote.asset_base == "BTC"
        assert quote.quality == "full"

    def test_partial_on_divergence(self) -> None:
        quote = normalize_hourly_quote(
            asset_base="ETH",
            sampled_at=datetime.now(timezone.utc),
            primary_price_usd=Decimal("100"),
            primary_provider="coingecko",
            primary_key_id="primary",
            volume_24h_usd=Decimal("1"),
            market_cap_usd=Decimal("1"),
            cross_price_usd=Decimal("110"),
            cross_provider="pyth",
            divergence_threshold_pct=0.5,
        )
        assert quote.quality == "partial"
        assert "cross_source_divergence" in quote.degraded_reasons

    def test_degraded_without_price(self) -> None:
        quote = normalize_hourly_quote(
            asset_base="SOL",
            sampled_at=datetime.now(timezone.utc),
            primary_price_usd=Decimal("0"),
            primary_provider="coingecko",
            primary_key_id="primary",
            volume_24h_usd=None,
            market_cap_usd=None,
            cross_price_usd=None,
            cross_provider=None,
            divergence_threshold_pct=0.5,
        )
        assert quote.quality == "degraded"
