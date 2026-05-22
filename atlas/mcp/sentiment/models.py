"""Pydantic models for Sentiment Synthesis MCP."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from atlas.providers.sentiment.alternative_me_provider import FearGreedFlag


class SentimentSourceWeights(BaseModel):
    """Canonical three-source blend for sentiment pillar."""

    model_config = ConfigDict(frozen=True)

    alternative_me_fg: float = Field(default=0.50, description="Alternative.me F&G weight")
    sentiment_nlp: float = Field(default=0.30, description="SentimentNews NLP weight")
    funding_proxy: float = Field(default=0.20, description="Funding z-score proxy weight")


class SentimentSynthesisResult(BaseModel):
    """Output of sentiment synthesis for agents and dashboards."""

    model_config = ConfigDict(frozen=True)

    fg_index: int = Field(description="Fear & Greed index 0–100")
    sentiment_score: int = Field(description="Signed weighted blend −35..+35")
    flag: FearGreedFlag = Field(description="Primary F&G regime flag")
    social_volume_gate_met: bool = Field(
        description="True when social_volume_zscore >= 2.0",
    )
    archetype_flags: list[str] = Field(
        default_factory=list,
        description="RETAIL_DESPAIR, FOMO_PEAK, MAXIMUM_GREED when gate met",
    )
    data_sources: list[str] = Field(
        default_factory=lambda: ["alternative_me", "sentiment_nlp", "funding_proxy"],
    )
    source_weights: SentimentSourceWeights = Field(
        default_factory=SentimentSourceWeights,
    )
