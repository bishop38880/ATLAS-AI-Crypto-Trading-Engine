"""
Tests for Kalshi client price normalisation.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Verifies that Kalshi cents (0–100) are correctly normalised to the
0.0–1.0 probability range used by the unified schema.
"""

from __future__ import annotations

import pytest

from .clients.kalshi_client import KalshiClient


class TestNormaliseCentsToProbability:
    """Tests for Kalshi cents-to-probability normalisation."""

    def test_fifty_cents_returns_half(self) -> None:
        """50 cents = 0.5 probability."""
        assert KalshiClient.normalise_cents_to_probability(50.0) == 0.5

    def test_zero_cents_returns_zero(self) -> None:
        """0 cents = 0.0 probability."""
        assert KalshiClient.normalise_cents_to_probability(0.0) == 0.0

    def test_hundred_cents_returns_one(self) -> None:
        """100 cents = 1.0 probability."""
        assert KalshiClient.normalise_cents_to_probability(100.0) == 1.0

    def test_negative_cents_clamps_to_zero(self) -> None:
        """Negative cents clamp to 0.0."""
        assert KalshiClient.normalise_cents_to_probability(-10.0) == 0.0

    def test_above_hundred_clamps_to_one(self) -> None:
        """Cents above 100 clamp to 1.0."""
        assert KalshiClient.normalise_cents_to_probability(150.0) == 1.0

    def test_fractional_cents(self) -> None:
        """Fractional cents normalise correctly."""
        result: float = KalshiClient.normalise_cents_to_probability(33.5)
        assert result == pytest.approx(0.335, abs=1e-9)


class TestKalshiGracefulDegradation:
    """Tests for Kalshi client degraded mode without API key."""

    def test_no_key_reports_unavailable(self) -> None:
        """Client without API key reports unavailable."""
        client: KalshiClient = KalshiClient(api_key="")
        assert client.is_available is False

    def test_with_key_reports_available(self) -> None:
        """Client with API key reports available."""
        client: KalshiClient = KalshiClient(api_key="test-key-123")
        assert client.is_available is True

    @pytest.mark.asyncio
    async def test_search_without_key_returns_empty(self) -> None:
        """Search with no key returns empty list gracefully."""
        client: KalshiClient = KalshiClient(api_key="")
        results: list = await client.search_events("fed rate")
        assert results == []

    @pytest.mark.asyncio
    async def test_orderbook_without_key_returns_empty(self) -> None:
        """Order book fetch with no key returns empty dict."""
        client: KalshiClient = KalshiClient(api_key="")
        result: dict = await client.fetch_order_book("FAKE-TICKER")
        assert result == {}
