"""Tests for Sentiment Synthesis MCP tool."""

from __future__ import annotations

from atlas.mcp.sentiment.server import synthesize_sentiment


def test_synthesize_sentiment_gate_blocks() -> None:
    """Sub-threshold social volume zeroes sentiment_score."""
    result = synthesize_sentiment(
        fg_index=10,
        social_volume_zscore=1.0,
    )
    assert result.sentiment_score == 0
    assert result.social_volume_gate_met is False
    assert result.archetype_flags == []


def test_synthesize_sentiment_retail_despair() -> None:
    """RETAIL_DESPAIR when fg <= 20 and gate met."""
    result = synthesize_sentiment(
        fg_index=15,
        social_volume_zscore=2.5,
    )
    assert result.social_volume_gate_met is True
    assert "RETAIL_DESPAIR" in result.archetype_flags
    assert result.source_weights.alternative_me_fg == 0.50


def test_synthesize_sentiment_fomo_peak() -> None:
    """FOMO_PEAK when fg >= 80 and gate met."""
    result = synthesize_sentiment(
        fg_index=82,
        social_volume_zscore=3.0,
    )
    assert "FOMO_PEAK" in result.archetype_flags
