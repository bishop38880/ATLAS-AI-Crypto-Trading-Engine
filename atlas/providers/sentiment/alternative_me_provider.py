"""Alternative.me Crypto Fear & Greed Index — canonical sentiment scoring.

Strategy Document v1.1 §3.3 thresholds map F&G to signed sentiment points (−35..+35).
Social-volume gate (z ≥ 2.0) is enforced by SentimentAgent / SentimentSynthesisMCP.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import msgspec
from loguru import logger
from pydantic import ConfigDict
from pydantic import BaseModel, Field

from atlas.shared.config import PolarisSettings

FearGreedFlag = Literal[
    "MAXIMUM_FEAR",
    "EXTREME_FEAR",
    "NEUTRAL",
    "EXTREME_GREED",
    "MAXIMUM_GREED",
]

SOURCE_WEIGHT_ALTERNATIVE_ME: float = 0.50
SOURCE_WEIGHT_SENTIMENT_NLP: float = 0.30
SOURCE_WEIGHT_FUNDING_PROXY: float = 0.20

SOCIAL_VOLUME_GATE_ZSCORE: float = 2.0


class FearGreedResponse(msgspec.Struct, frozen=True):
    """Single F&G reading from Alternative.me API."""

    value: str
    value_classification: str
    timestamp: str


class FearGreedResult(msgspec.Struct, frozen=True):
    """Top-level Alternative.me /fng/ JSON envelope."""

    name: str
    data: list[FearGreedResponse]
    metadata: dict[str, str]


class AlternativeMeSentimentOutput(BaseModel):
    """Scored sentiment payload for agents and MCP synthesis."""

    model_config = ConfigDict(frozen=True)

    fg_index: int = Field(description="Fear & Greed index 0–100")
    classification: str = Field(description="API value_classification string")
    sentiment_score: int = Field(
        description="Signed sentiment contribution −35..+35 per Strategy Doc §3.3",
    )
    flag: FearGreedFlag = Field(description="Discrete F&G regime flag")
    social_volume_gate_required: bool = Field(
        default=True,
        description="Score is zeroed downstream when social volume gate is not met",
    )


class AlternativeMeProvider:
    """Fetches Fear & Greed from api.alternative.me/fng/ (no API key)."""

    API_URL = "https://api.alternative.me/fng/?limit=1&format=json"

    def __init__(self, settings: PolarisSettings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Open the shared HTTP client."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=5.0),
            http2=True,
        )
        logger.info("AlternativeMeProvider started")

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("AlternativeMeProvider stopped")

    async def fetch(self) -> AlternativeMeSentimentOutput:
        """Fetch latest F&G and map to signed sentiment score."""
        if self._client is None:
            msg = "Provider not started"
            raise RuntimeError(msg)
        try:
            response = await asyncio.wait_for(
                self._client.get(self.API_URL),
                timeout=10.0,
            )
            response.raise_for_status()
            raw = msgspec.json.decode(response.content, type=FearGreedResult)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("AlternativeMeProvider fetch failed | error={}", exc)
            raise

        if not raw.data:
            msg = "Alternative.me returned empty data array"
            raise ValueError(msg)

        entry = raw.data[0]
        fg_index = int(entry.value)
        output = compute_sentiment_from_fg_index(
            fg_index=fg_index,
            classification=entry.value_classification,
        )
        logger.info(
            "FearGreed fetched | fg={} | classification={} | score={}",
            fg_index,
            entry.value_classification,
            output.sentiment_score,
        )
        return output


def score_fg(fg: int) -> tuple[int, FearGreedFlag]:
    """Map F&G index to signed sentiment score and flag (Strategy Doc v1.1 §3.3)."""
    if fg <= 15:
        return 35, "MAXIMUM_FEAR"
    if fg <= 20:
        score = 30 - int((fg - 16) * 1.25)
        return score, "EXTREME_FEAR"
    if fg <= 79:
        return 0, "NEUTRAL"
    if fg <= 84:
        score = -(25 + int((fg - 80) * 1.0))
        return score, "EXTREME_GREED"
    return -35, "MAXIMUM_GREED"


def compute_sentiment_from_fg_index(
    fg_index: int,
    classification: str,
) -> AlternativeMeSentimentOutput:
    """Build output model from a known F&G index (no HTTP)."""
    sentiment_score, flag = score_fg(fg_index)
    return AlternativeMeSentimentOutput(
        fg_index=fg_index,
        classification=classification,
        sentiment_score=sentiment_score,
        flag=flag,
        social_volume_gate_required=True,
    )


def compute_weighted_sentiment_score(
    fg_sentiment_score: int,
    nlp_sentiment_score: int,
    funding_sentiment_score: int,
) -> int:
    """Blend three signed components using canonical source weights."""
    blended = (
        SOURCE_WEIGHT_ALTERNATIVE_ME * fg_sentiment_score
        + SOURCE_WEIGHT_SENTIMENT_NLP * nlp_sentiment_score
        + SOURCE_WEIGHT_FUNDING_PROXY * funding_sentiment_score
    )
    return int(round(blended))


def score_nlp_polarity_proxy(polarity_percentile: float) -> int:
    """Map NLP polarity percentile to signed −35..+35 proxy."""
    if polarity_percentile >= 85.0:
        return 35
    if polarity_percentile >= 65.0:
        return 20
    if polarity_percentile <= 15.0:
        return -35
    if polarity_percentile <= 35.0:
        return -20
    return 0


def score_funding_rate_proxy(funding_zscore: float) -> int:
    """Contrarian funding z-score proxy for sentiment pillar (−35..+35)."""
    if funding_zscore >= 2.5:
        return -35
    if funding_zscore >= 1.5:
        return -20
    if funding_zscore <= -2.5:
        return 35
    if funding_zscore <= -1.5:
        return 20
    return 0


def detect_archetype_flags(
    fg_index: int,
    social_volume_gate_met: bool,
) -> list[str]:
    """Return RETAIL_DESPAIR / FOMO_PEAK when gate and F&G tails align."""
    if not social_volume_gate_met:
        return []
    flags: list[str] = []
    if fg_index <= 20:
        flags.append("RETAIL_DESPAIR")
    if fg_index >= 80:
        flags.append("FOMO_PEAK")
    if fg_index >= 85:
        flags.append("MAXIMUM_GREED")
    return flags
