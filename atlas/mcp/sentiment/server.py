"""Sentiment Synthesis MCP — Alternative.me F&G + NLP + funding proxy.

Sources:
  - alternative_me Fear & Greed Index (50%)
  - SentimentNews NLP polarity (30%)
  - Funding rate z-score proxy (20%)

Archetypes (require social_volume_zscore >= 2.0):
  - RETAIL_DESPAIR when fg_index <= 20
  - FOMO_PEAK / MAXIMUM_GREED when fg_index >= 80
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from atlas.mcp.sentiment.models import SentimentSourceWeights, SentimentSynthesisResult
from atlas.providers.sentiment.alternative_me_provider import (
    SOCIAL_VOLUME_GATE_ZSCORE,
    SOURCE_WEIGHT_ALTERNATIVE_ME,
    SOURCE_WEIGHT_FUNDING_PROXY,
    SOURCE_WEIGHT_SENTIMENT_NLP,
    compute_sentiment_from_fg_index,
    compute_weighted_sentiment_score,
    detect_archetype_flags,
    score_funding_rate_proxy,
    score_nlp_polarity_proxy,
)

SOURCE_WEIGHTS = SentimentSourceWeights(
    alternative_me_fg=SOURCE_WEIGHT_ALTERNATIVE_ME,
    sentiment_nlp=SOURCE_WEIGHT_SENTIMENT_NLP,
    funding_proxy=SOURCE_WEIGHT_FUNDING_PROXY,
)

mcp = FastMCP(
    "SentimentSynthesis",
    instructions=(
        "Synthesises sentiment from Alternative.me Fear & Greed (50%), "
        "NLP polarity percentile (30%), and funding-rate z-score proxy (20%). "
        "Social volume gate: z-score >= 2.0 required for non-zero sentiment_score."
    ),
)


@mcp.tool()
def synthesize_sentiment(
    fg_index: int,
    fg_classification: str = "Neutral",
    polarity_percentile: float = 50.0,
    funding_rate_zscore: float = 0.0,
    social_volume_zscore: float = 0.0,
) -> SentimentSynthesisResult:
    """Blend sentiment sources and emit archetype flags when the volume gate passes.

    Args:
        fg_index: Fear & Greed index (0–100) from Alternative.me.
        fg_classification: API classification string.
        polarity_percentile: NLP polarity percentile (0–100).
        funding_rate_zscore: Funding rate z-score for contrarian proxy.
        social_volume_zscore: Social volume z-score; must be >= 2.0 to unlock score.
    """
    gate_met = social_volume_zscore >= SOCIAL_VOLUME_GATE_ZSCORE
    fg_output = compute_sentiment_from_fg_index(fg_index, fg_classification)

    if not gate_met:
        return SentimentSynthesisResult(
            fg_index=fg_index,
            sentiment_score=0,
            flag=fg_output.flag,
            social_volume_gate_met=False,
            archetype_flags=[],
            source_weights=SOURCE_WEIGHTS,
        )

    nlp_score = score_nlp_polarity_proxy(polarity_percentile)
    funding_score = score_funding_rate_proxy(funding_rate_zscore)
    blended = compute_weighted_sentiment_score(
        fg_output.sentiment_score,
        nlp_score,
        funding_score,
    )
    archetypes = detect_archetype_flags(fg_index, social_volume_gate_met=True)

    return SentimentSynthesisResult(
        fg_index=fg_index,
        sentiment_score=blended,
        flag=fg_output.flag,
        social_volume_gate_met=True,
        archetype_flags=archetypes,
        source_weights=SOURCE_WEIGHTS,
    )
