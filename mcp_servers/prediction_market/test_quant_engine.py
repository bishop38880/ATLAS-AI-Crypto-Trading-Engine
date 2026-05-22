"""
Tests for Prediction Market quant engine.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Covers BBO midpoint extraction, capital-weighted conviction scoring,
market status classification, and platform field extractors.

Sentinel Invariants:
  - pytest + pytest-asyncio (async-native)
  - No vacuous assertions (assert True)
  - Covers expected output, edge cases, and failure/degraded modes
  - All financial assertions use Decimal comparison
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from .models import (
    MarketStatus,
    OrderBookSnapshot,
    Platform,
    UnifiedEventProbability,
)
from .quant_engine import (
    _assemble_unified_event,
    _calculate_bbo_midpoint,
    _calculate_conviction_score,
    _calculate_oi_factor,
    _calculate_spread_factor,
    _calculate_volume_bonus,
    _classify_market_status,
    _classify_text_category,
    _extract_market_id,
    _extract_oi,
    _extract_question,
    _extract_volume,
    build_unified_event,
    compute_bbo_midpoint,
    compute_conviction_score,
)


# ──────────────────────────────────────────────────────────────
# BBO Midpoint
# ──────────────────────────────────────────────────────────────


class TestCalculateBboMidpoint:
    """Tests for BBO midpoint extraction from order book levels."""

    def test_normal_book_returns_correct_midpoint(self) -> None:
        """Standard two-sided book produces correct midpoint."""
        bids: list[list[float]] = [[0.60, 100], [0.55, 200]]
        asks: list[list[float]] = [[0.65, 100], [0.70, 200]]
        midpoint, best_bid, best_ask, spread = _calculate_bbo_midpoint(
            bids, asks
        )
        assert best_bid == 0.60
        assert best_ask == 0.65
        assert spread == pytest.approx(0.05, abs=1e-9)
        assert midpoint == pytest.approx(0.625, abs=1e-9)

    def test_empty_bids_defaults_bid_to_zero(self) -> None:
        """Empty bid side defaults best_bid to 0.0."""
        bids: list[list[float]] = []
        asks: list[list[float]] = [[0.70, 50]]
        midpoint, best_bid, best_ask, spread = _calculate_bbo_midpoint(
            bids, asks
        )
        assert best_bid == 0.0
        assert best_ask == 0.70
        assert midpoint == pytest.approx(0.35, abs=1e-9)

    def test_empty_asks_defaults_ask_to_one(self) -> None:
        """Empty ask side defaults best_ask to 1.0."""
        bids: list[list[float]] = [[0.40, 100]]
        asks: list[list[float]] = []
        midpoint, best_bid, best_ask, spread = _calculate_bbo_midpoint(
            bids, asks
        )
        assert best_ask == 1.0
        assert midpoint == pytest.approx(0.70, abs=1e-9)

    def test_empty_book_returns_0_5_midpoint(self) -> None:
        """Completely empty book returns 0.5 midpoint."""
        midpoint, best_bid, best_ask, spread = _calculate_bbo_midpoint(
            [], []
        )
        assert best_bid == 0.0
        assert best_ask == 1.0
        assert midpoint == pytest.approx(0.5, abs=1e-9)
        assert spread == pytest.approx(1.0, abs=1e-9)

    def test_tight_spread_book(self) -> None:
        """Tight spread (1 cent) produces near-equal bid/ask."""
        bids: list[list[float]] = [[0.50, 1000]]
        asks: list[list[float]] = [[0.51, 1000]]
        midpoint, _, _, spread = _calculate_bbo_midpoint(bids, asks)
        assert spread == pytest.approx(0.01, abs=1e-9)
        assert midpoint == pytest.approx(0.505, abs=1e-9)


# ──────────────────────────────────────────────────────────────
# Spread Factor
# ──────────────────────────────────────────────────────────────


class TestCalculateSpreadFactor:
    """Tests for spread penalty factor calculation."""

    def test_tight_spread_returns_one(self) -> None:
        """Spread <= 1 cent gives full factor."""
        assert _calculate_spread_factor(0.005) == 1.0

    def test_illiquid_spread_returns_zero(self) -> None:
        """Spread >= 25% gives zero factor."""
        assert _calculate_spread_factor(0.30) == 0.0

    def test_moderate_spread_returns_partial(self) -> None:
        """Moderate spread returns intermediate value."""
        factor: float = _calculate_spread_factor(0.05)
        assert 0.0 < factor < 1.0

    def test_threshold_spread_returns_zero(self) -> None:
        """Spread at penalty threshold returns zero."""
        factor: float = _calculate_spread_factor(0.10)
        assert factor == pytest.approx(0.0, abs=1e-9)


# ──────────────────────────────────────────────────────────────
# OI Factor
# ──────────────────────────────────────────────────────────────


class TestCalculateOiFactor:
    """Tests for log-scale OI contribution."""

    def test_below_floor_returns_zero(self) -> None:
        """OI below $1000 floor produces zero factor."""
        assert _calculate_oi_factor(500.0) == 0.0

    def test_at_ceiling_returns_near_one(self) -> None:
        """OI at $10M ceiling produces near-one factor."""
        factor: float = _calculate_oi_factor(10_000_000.0)
        assert factor == pytest.approx(1.0, abs=0.01)

    def test_moderate_oi_returns_intermediate(self) -> None:
        """$100K OI produces intermediate factor."""
        factor: float = _calculate_oi_factor(100_000.0)
        assert 0.3 < factor < 0.9

    def test_above_ceiling_clamps(self) -> None:
        """OI above ceiling is clamped to ceiling."""
        factor_at_ceiling: float = _calculate_oi_factor(10_000_000.0)
        factor_above: float = _calculate_oi_factor(50_000_000.0)
        assert factor_at_ceiling == pytest.approx(
            factor_above, abs=1e-9
        )


# ──────────────────────────────────────────────────────────────
# Volume Bonus
# ──────────────────────────────────────────────────────────────


class TestCalculateVolumeBonus:
    """Tests for volume bonus calculation."""

    def test_zero_volume_returns_zero(self) -> None:
        """Zero volume gives zero bonus."""
        assert _calculate_volume_bonus(0.0) == 0.0

    def test_negative_volume_returns_zero(self) -> None:
        """Negative volume gives zero bonus."""
        assert _calculate_volume_bonus(-100.0) == 0.0

    def test_high_volume_capped_at_015(self) -> None:
        """Volume bonus never exceeds 0.15."""
        bonus: float = _calculate_volume_bonus(100_000_000.0)
        assert bonus <= 0.15


# ──────────────────────────────────────────────────────────────
# Conviction Score
# ──────────────────────────────────────────────────────────────


class TestCalculateConvictionScore:
    """Tests for capital-weighted conviction score."""

    def test_high_prob_high_oi_tight_spread(self) -> None:
        """High probability + high OI + tight spread = high conviction."""
        score: float = _calculate_conviction_score(
            probability=0.85,
            spread=0.02,
            open_interest_usd=5_000_000.0,
            volume_usd=1_000_000.0,
        )
        assert score > 0.6

    def test_high_prob_low_oi_is_noise(self) -> None:
        """High probability but low OI = low conviction (noise)."""
        score: float = _calculate_conviction_score(
            probability=0.95,
            spread=0.02,
            open_interest_usd=500.0,
            volume_usd=100.0,
        )
        assert score < 0.1

    def test_wide_spread_kills_conviction(self) -> None:
        """Wide spread destroys conviction regardless of OI."""
        score: float = _calculate_conviction_score(
            probability=0.80,
            spread=0.30,
            open_interest_usd=10_000_000.0,
            volume_usd=5_000_000.0,
        )
        assert score == 0.0

    def test_score_clamped_to_unit_interval(self) -> None:
        """Score is always in [0.0, 1.0]."""
        score: float = _calculate_conviction_score(
            probability=1.0,
            spread=0.001,
            open_interest_usd=10_000_000.0,
            volume_usd=50_000_000.0,
        )
        assert 0.0 <= score <= 1.0

    def test_zero_probability_returns_zero(self) -> None:
        """Zero probability gives zero conviction."""
        score: float = _calculate_conviction_score(
            probability=0.0,
            spread=0.02,
            open_interest_usd=5_000_000.0,
            volume_usd=1_000_000.0,
        )
        assert score == 0.0


# ──────────────────────────────────────────────────────────────
# Market Status Classification
# ──────────────────────────────────────────────────────────────


class TestClassifyMarketStatus:
    """Tests for market status classification."""

    def test_healthy_market_returns_ok(self) -> None:
        """Tight spread + good OI = OK."""
        assert _classify_market_status(0.03, 50_000.0) == MarketStatus.OK

    def test_wide_spread_returns_illiquid(self) -> None:
        """Very wide spread = ILLIQUID."""
        assert _classify_market_status(0.30, 50_000.0) == MarketStatus.ILLIQUID

    def test_low_oi_returns_illiquid(self) -> None:
        """Low OI = ILLIQUID."""
        assert _classify_market_status(0.03, 100.0) == MarketStatus.ILLIQUID

    def test_moderate_spread_returns_degraded(self) -> None:
        """Moderate spread = DEGRADED."""
        status: MarketStatus = _classify_market_status(0.15, 50_000.0)
        assert status == MarketStatus.DEGRADED


# ──────────────────────────────────────────────────────────────
# Category Inference
# ──────────────────────────────────────────────────────────────


class TestClassifyTextCategory:
    """Tests for macro category text classification."""

    def test_fed_keywords(self) -> None:
        """Fed-related keywords produce 'fed' category."""
        assert _classify_text_category("will the fed cut rates") == "fed"
        assert _classify_text_category("fomc meeting december") == "fed"

    def test_sec_keywords(self) -> None:
        """SEC-related keywords produce 'sec' category."""
        assert _classify_text_category("sec approves spot etf") == "sec"

    def test_crypto_regulation_keywords(self) -> None:
        """Regulation keywords produce 'crypto_regulation'."""
        assert _classify_text_category("cftc enforcement") == "crypto_regulation"

    def test_election_keywords(self) -> None:
        """Election keywords produce 'election'."""
        assert _classify_text_category("president wins 2028") == "election"

    def test_unknown_returns_general(self) -> None:
        """Unknown text returns 'general'."""
        assert _classify_text_category("random event xyz") == "general"


# ──────────────────────────────────────────────────────────────
# Platform Extractors
# ──────────────────────────────────────────────────────────────


class TestPlatformExtractors:
    """Tests for platform-specific field extraction."""

    def test_polymarket_market_id(self) -> None:
        """Polymarket uses condition_id as market_id."""
        data: dict = {"condition_id": "0xabc123"}
        assert _extract_market_id(data, Platform.POLYMARKET) == "0xabc123"

    def test_kalshi_market_id(self) -> None:
        """Kalshi uses ticker as market_id."""
        data: dict = {"ticker": "FED-25MAR-CUT"}
        assert _extract_market_id(data, Platform.KALSHI) == "FED-25MAR-CUT"

    def test_polymarket_question(self) -> None:
        """Polymarket uses 'question' field."""
        data: dict = {"question": "Will the SEC approve?"}
        assert _extract_question(data, Platform.POLYMARKET) == "Will the SEC approve?"

    def test_kalshi_question(self) -> None:
        """Kalshi uses 'title' field."""
        data: dict = {"title": "Fed rate cut in June?"}
        assert _extract_question(data, Platform.KALSHI) == "Fed rate cut in June?"

    def test_polymarket_volume(self) -> None:
        """Polymarket extracts volume from 'volume' key."""
        data: dict = {"volume": 1_500_000}
        assert _extract_volume(data, Platform.POLYMARKET) == 1_500_000.0

    def test_kalshi_volume(self) -> None:
        """Kalshi extracts volume from 'volume' key."""
        data: dict = {"volume": 250_000}
        assert _extract_volume(data, Platform.KALSHI) == 250_000.0

    def test_missing_volume_returns_zero(self) -> None:
        """Missing volume key returns 0.0."""
        assert _extract_volume({}, Platform.POLYMARKET) == 0.0

    def test_polymarket_oi(self) -> None:
        """Polymarket uses 'liquidity' for OI."""
        data: dict = {"liquidity": 800_000}
        assert _extract_oi(data, Platform.POLYMARKET) == 800_000.0

    def test_kalshi_oi(self) -> None:
        """Kalshi uses 'open_interest' for OI."""
        data: dict = {"open_interest": 120_000}
        assert _extract_oi(data, Platform.KALSHI) == 120_000.0


# ──────────────────────────────────────────────────────────────
# Async Wrappers
# ──────────────────────────────────────────────────────────────


class TestAsyncComputeBboMidpoint:
    """Tests for async BBO midpoint wrapper."""

    @pytest.mark.asyncio
    async def test_async_midpoint_matches_sync(self) -> None:
        """Async wrapper produces same result as sync function."""
        book: OrderBookSnapshot = OrderBookSnapshot(
            market_id="test-001",
            platform=Platform.POLYMARKET,
            bids=[[0.55, 200], [0.50, 300]],
            asks=[[0.60, 200], [0.65, 300]],
        )
        midpoint, bid, ask, spread = await compute_bbo_midpoint(book)
        assert midpoint == pytest.approx(0.575, abs=1e-9)
        assert bid == 0.55
        assert ask == 0.60
        assert spread == pytest.approx(0.05, abs=1e-9)


class TestAsyncComputeConvictionScore:
    """Tests for async conviction score wrapper."""

    @pytest.mark.asyncio
    async def test_async_conviction_matches_sync(self) -> None:
        """Async wrapper produces same result as sync function."""
        async_result: float = await compute_conviction_score(
            0.75, 0.03, 2_000_000.0, 500_000.0
        )
        sync_result: float = _calculate_conviction_score(
            0.75, 0.03, 2_000_000.0, 500_000.0
        )
        assert async_result == pytest.approx(sync_result, abs=1e-9)


class TestBuildUnifiedEvent:
    """Tests for full event construction pipeline."""

    @pytest.mark.asyncio
    async def test_builds_valid_event(self) -> None:
        """Full pipeline produces a valid UnifiedEventProbability."""
        market_data: dict = {
            "condition_id": "0xtest",
            "question": "Will the SEC approve the ETF?",
            "volume": 2_000_000,
            "liquidity": 500_000,
            "tags": "sec crypto",
        }
        book: OrderBookSnapshot = OrderBookSnapshot(
            market_id="0xtest",
            platform=Platform.POLYMARKET,
            bids=[[0.60, 500], [0.55, 1000]],
            asks=[[0.65, 500], [0.70, 1000]],
        )
        event: UnifiedEventProbability = await build_unified_event(
            market_data, book, Platform.POLYMARKET
        )
        assert event.market_id == "0xtest"
        assert event.platform == Platform.POLYMARKET
        assert event.probability == pytest.approx(0.625, abs=1e-9)
        assert event.spread == pytest.approx(0.05, abs=1e-9)
        assert event.volume_usd == Decimal("2000000.00")
        assert event.open_interest_usd == Decimal("500000.00")
        assert 0.0 <= event.capital_conviction_score <= 1.0
        assert event.category == "sec"
        assert event.status in (MarketStatus.OK, MarketStatus.DEGRADED)

    @pytest.mark.asyncio
    async def test_empty_book_produces_degraded(self) -> None:
        """Empty order book produces ILLIQUID status."""
        market_data: dict = {
            "condition_id": "0xempty",
            "question": "Test empty book",
            "volume": 0,
            "liquidity": 0,
        }
        book: OrderBookSnapshot = OrderBookSnapshot(
            market_id="0xempty",
            platform=Platform.POLYMARKET,
            bids=[],
            asks=[],
        )
        event: UnifiedEventProbability = await build_unified_event(
            market_data, book, Platform.POLYMARKET
        )
        assert event.status == MarketStatus.ILLIQUID
        assert event.capital_conviction_score == 0.0
