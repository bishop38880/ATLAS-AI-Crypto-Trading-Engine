"""POLARIS confluence scoring — v6.1 canonical constants.

Single source of truth for category ceilings, signal tiers, and sizing
percentages used by the deterministic engine and LLM context assembly.
White Paper § scoring narrative must match; changelog any edits.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Final, Literal

from pydantic import BaseModel, Field

SCORING_VERSION: Final[str] = "6.1"
TOTAL_POINTS: Final[int] = 220

# Keys align with ``ConfluenceScoringEngine`` breakdown categories.
CATEGORY_MAX_POINTS: Final[dict[str, int]] = {
    "derivatives": 75,
    "onchain": 65,
    "technical": 15,
    "sentiment": 35,
    "market_context": 30,
}

# Substrings in ``agent_name`` map to these buckets in ``context_assembler``.
AGENT_VERDICT_CATEGORY_ORDER: Final[tuple[str, ...]] = (
    "derivatives",
    "technical",
    "onchain",
    "sentiment",
    "market_context",
)

AGENT_VERDICT_CATEGORY_MAX_POINTS: Final[dict[str, int]] = {
    "derivatives": CATEGORY_MAX_POINTS["derivatives"],
    "technical": CATEGORY_MAX_POINTS["technical"],
    "onchain": CATEGORY_MAX_POINTS["onchain"],
    "sentiment": CATEGORY_MAX_POINTS["sentiment"],
    "market_context": CATEGORY_MAX_POINTS["market_context"],
}

SignalStrengthLabel = Literal["STRONG", "MODERATE", "WEAK", "NO_TRADE"]

STRONG_MIN: Final[int] = 180
MODERATE_MIN: Final[int] = 150
WEAK_MIN: Final[int] = 120


class FundingNonLinearGate(BaseModel, frozen=True):
    """Absolute funding-rate gate (fraction of notional per funding period)."""

    threshold: Decimal = Field(
        default=Decimal("0.0005"),
        description="If |funding_rate| > 0.05%, apply non-linear penalty to derivatives.",
    )


class SentimentEventGate(BaseModel, frozen=True):
    """Sentiment pillar is mostly inactive unless an event tail is detected."""

    active_pct: int = Field(
        default=10,
        ge=0,
        le=100,
        description="Approximate share of time sentiment is unlocked (~10%).",
    )


class ScoringGates(BaseModel, frozen=True):
    """Named production gates for documentation and MCP parity."""

    funding_rate_nonlinear: FundingNonLinearGate = Field(
        default_factory=FundingNonLinearGate,
    )
    sentiment_event: SentimentEventGate = Field(
        default_factory=SentimentEventGate,
    )


DEFAULT_GATES: Final[ScoringGates] = ScoringGates()


def classify_signal_strength(total_score: int) -> SignalStrengthLabel:
    """Map raw confluence total to trade tier label."""
    if total_score >= STRONG_MIN:
        return "STRONG"
    if total_score >= MODERATE_MIN:
        return "MODERATE"
    if total_score >= WEAK_MIN:
        return "WEAK"
    return "NO_TRADE"


def get_position_size_pct(total_score: int) -> float:
    """Baseline position size (% of capital) before PROMETHEUS overlays."""
    if total_score >= STRONG_MIN:
        return 5.0
    if total_score >= MODERATE_MIN:
        return 3.0
    if total_score >= WEAK_MIN:
        return 2.0
    return 0.0


def get_leverage_reference(total_score: int) -> int:
    """Reference max leverage for tier (PROMETHEUS may clamp further)."""
    if total_score >= STRONG_MIN:
        return 5
    if total_score >= MODERATE_MIN:
        return 3
    if total_score >= WEAK_MIN:
        return 2
    return 0
