"""Sentiment providers — Alternative.me Fear & Greed canonical input."""

from atlas.providers.sentiment.alternative_me_provider import (
    AlternativeMeProvider,
    AlternativeMeSentimentOutput,
    compute_sentiment_from_fg_index,
    score_fg,
)

__all__ = [
    "AlternativeMeProvider",
    "AlternativeMeSentimentOutput",
    "compute_sentiment_from_fg_index",
    "score_fg",
]
