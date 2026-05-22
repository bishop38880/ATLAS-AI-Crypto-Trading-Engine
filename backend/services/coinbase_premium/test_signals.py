"""Unit tests for Coinbase premium signal extraction."""

from __future__ import annotations

import time

import pytest

from backend.services.coinbase_premium.signals import (
    calculate_premium_pct,
    calculate_score_contribution,
    compute_signals,
    _classify,
)


class TestCalculatePremiumPct:
    def test_positive_premium(self) -> None:
        result = calculate_premium_pct(67_500.0, 67_400.0)
        assert result is not None
        assert result > 0

    def test_zero_binance_returns_none(self) -> None:
        assert calculate_premium_pct(100.0, 0.0) is None


class TestClassifyPremium:
    def test_strong_us_buying(self) -> None:
        assert _classify(0.20) == "strong_us_buying"

    def test_neutral(self) -> None:
        assert _classify(0.0) == "neutral"

    def test_offshore_fragile(self) -> None:
        assert _classify(-0.20) == "offshore_driven_fragile"


class TestScoreContribution:
    def test_strong_positive_caps_at_eight(self) -> None:
        assert calculate_score_contribution(0.20, None) == 8.0

    def test_neutral_is_zero(self) -> None:
        assert calculate_score_contribution(0.0, None) == 0.0

    def test_strong_negative_caps_at_negative_eight(self) -> None:
        assert calculate_score_contribution(-0.20, None) == -8.0

    def test_zscore_amplifies_strong_positive(self) -> None:
        base = calculate_score_contribution(0.15, None)
        amplified = calculate_score_contribution(0.15, 2.5)
        assert amplified >= base


class TestComputeSignals:
    def test_rolling_averages_from_history(self) -> None:
        now = time.time()
        history = [(now - 30, 0.10), (now - 10, 0.12)]
        signals = compute_signals(history, current_premium=0.12)
        assert signals["current_premium_pct"] == 0.12
        assert signals["avg_1min"] is not None
        assert signals["scoring_contribution"] is not None

    def test_insufficient_history_trend(self) -> None:
        signals = compute_signals([], current_premium=0.0)
        assert signals["trend"] == "insufficient_data"
        assert signals["zscore_1hr"] is None
