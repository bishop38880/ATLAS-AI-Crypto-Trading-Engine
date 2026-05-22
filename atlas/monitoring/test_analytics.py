"""Tests for hourly analytics calculations."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from atlas.monitoring.analytics import (
    calculate_basket_breadth,
    calculate_hourly_return_pct,
    calculate_momentum_pct,
    calculate_rank_changes,
    calculate_relative_strength,
    calculate_rolling_volatility_pct,
    calculate_volume_z_score,
)
from atlas.monitoring.models import NormalizedHourlyQuote


class TestHourlyAnalytics:
    def test_hourly_return_positive(self) -> None:
        result = calculate_hourly_return_pct(Decimal("110"), Decimal("100"))
        assert result is not None
        assert abs(result - 10.0) < 0.01

    def test_hourly_return_missing_previous(self) -> None:
        assert calculate_hourly_return_pct(Decimal("100"), None) is None

    def test_rolling_volatility(self) -> None:
        values = [1.0, -1.0, 2.0, -2.0]
        vol = calculate_rolling_volatility_pct(values)
        assert vol is not None
        assert vol > 0

    def test_momentum_requires_history(self) -> None:
        prices = [Decimal("100")] * 5
        assert calculate_momentum_pct(prices, lookback=6) is None

    def test_volume_z_score(self) -> None:
        history = [Decimal("100"), Decimal("110"), Decimal("90"), Decimal("105")]
        z = calculate_volume_z_score(Decimal("200"), history)
        assert z is not None
        assert z > 0

    def test_relative_strength(self) -> None:
        assert calculate_relative_strength(5.0, 2.0) == 3.0

    def test_rank_change_improved_rank(self) -> None:
        changes = calculate_rank_changes({"BTC": 1}, {"BTC": 3})
        assert changes["BTC"] == 2

    def test_basket_breadth(self) -> None:
        breadth = calculate_basket_breadth([1.0, -0.5, 2.0, None])
        assert abs(breadth - 66.666) < 0.1

    def test_build_asset_analytics_smoke(self) -> None:
        from atlas.monitoring.analytics import build_asset_analytics

        quote = NormalizedHourlyQuote(
            asset_base="BTC",
            sampled_at=datetime.now(timezone.utc),
            price_usd=Decimal("100"),
            volume_24h_usd=Decimal("1000"),
            market_cap_usd=Decimal("1e12"),
            source_provider="coingecko",
            source_key_id="primary",
        )
        row = build_asset_analytics(
            quote=quote,
            previous_price=Decimal("90"),
            return_history=[1.0, 2.0],
            price_history=[Decimal("80"), Decimal("90"), Decimal("100")],
            volume_history=[Decimal("900"), Decimal("950")],
            market_cap_rank=1,
            rank_change=0,
            basket_return_pct=1.0,
        )
        assert row.hourly_return_pct is not None
