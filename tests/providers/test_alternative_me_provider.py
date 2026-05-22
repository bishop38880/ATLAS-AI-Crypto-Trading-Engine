"""Unit tests for Alternative.me Fear & Greed sentiment scoring."""

from __future__ import annotations

import pytest

from atlas.providers.sentiment.alternative_me_provider import (
    compute_weighted_sentiment_score,
    detect_archetype_flags,
    score_fg,
    score_funding_rate_proxy,
    score_nlp_polarity_proxy,
)


@pytest.mark.parametrize(
    "fg,expected_score,expected_flag",
    [
        (5, 35, "MAXIMUM_FEAR"),
        (15, 35, "MAXIMUM_FEAR"),
        (16, 30, "EXTREME_FEAR"),
        (20, 25, "EXTREME_FEAR"),
        (21, 0, "NEUTRAL"),
        (50, 0, "NEUTRAL"),
        (79, 0, "NEUTRAL"),
        (80, -25, "EXTREME_GREED"),
        (84, -29, "EXTREME_GREED"),
        (85, -35, "MAXIMUM_GREED"),
        (100, -35, "MAXIMUM_GREED"),
    ],
)
def test_score_fg(fg: int, expected_score: int, expected_flag: str) -> None:
    """F&G index maps to Strategy Doc §3.3 score and flag."""
    score, flag = score_fg(fg)
    assert score == expected_score
    assert flag == expected_flag


def test_compute_weighted_sentiment_score_blend() -> None:
    """50/30/20 weights blend signed components."""
    result = compute_weighted_sentiment_score(
        fg_sentiment_score=20,
        nlp_sentiment_score=10,
        funding_sentiment_score=-10,
    )
    assert result == int(round(0.5 * 20 + 0.3 * 10 + 0.2 * -10))


def test_score_nlp_polarity_proxy_tails() -> None:
    """NLP proxy hits extremes at high/low percentiles."""
    assert score_nlp_polarity_proxy(90.0) == 35
    assert score_nlp_polarity_proxy(10.0) == -35
    assert score_nlp_polarity_proxy(50.0) == 0


def test_score_funding_rate_proxy_contrarian() -> None:
    """High positive funding z maps bearish; deep negative maps bullish."""
    assert score_funding_rate_proxy(2.6) == -35
    assert score_funding_rate_proxy(-2.6) == 35


def test_detect_archetype_flags_gated() -> None:
    """Archetype flags require social volume gate."""
    assert detect_archetype_flags(10, social_volume_gate_met=False) == []
    despair = detect_archetype_flags(15, social_volume_gate_met=True)
    assert "RETAIL_DESPAIR" in despair
    greed = detect_archetype_flags(85, social_volume_gate_met=True)
    assert "FOMO_PEAK" in greed
    assert "MAXIMUM_GREED" in greed
