"""
Pydantic v2 frozen schemas for Prediction Market MCP.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Provides a unified ``UnifiedEventProbability`` schema that normalises
Polymarket (USDC, 0–1 price) and Kalshi (USD cents, yes_price) data
into a single agentic contract for ``NewsCatalystAgent`` and
``MacroCrossMarketAgent``.

Sentinel Invariants:
  - All models frozen=True (immutable DTOs)
  - Financial values (volume_usd, open_interest_usd) use Decimal
  - Dimensionless values (probability, conviction score) use float
  - Every field has Field(description=...)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    """Prediction market source platform."""

    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


class MarketStatus(str, Enum):
    """Operational status of a market data point."""

    OK = "OK"
    DEGRADED = "DEGRADED"
    ILLIQUID = "ILLIQUID"
    STALE = "STALE"


class UnifiedEventProbability(BaseModel, frozen=True):
    """
    Normalised prediction market event probability.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    This is the canonical schema consumed by ``NewsCatalystAgent`` and
    ``MacroCrossMarketAgent``. It abstracts away platform-specific pricing
    conventions (USDC vs USD cents) into a single normalised format.

    Interpretation guide for LLM agents:
      - ``probability``: 0.0–1.0 BBO midpoint (NOT last-trade price)
      - ``capital_conviction_score``: 0.0–1.0; scales probability by
        liquidity depth and spread tightness. Scores below 0.3 are noise.
      - ``open_interest_usd``: total capital committed; high OI with
        moderate probability is a stronger signal than high probability
        with low OI.
    """

    market_id: str = Field(
        description="Platform-specific market/contract identifier.",
    )
    platform: Platform = Field(
        description="Source platform: 'polymarket' or 'kalshi'.",
    )
    question: str = Field(
        description="Human-readable market question / event description.",
    )
    category: str = Field(
        default="general",
        description="Event category: 'fed', 'sec', 'election', 'crypto_regulation', 'general'.",
    )
    probability: float = Field(
        description="BBO midpoint probability (0.0–1.0). NOT last-trade price.",
    )
    best_bid: float = Field(
        description="Best bid price for YES token (0.0–1.0 normalised).",
    )
    best_ask: float = Field(
        description="Best ask price for YES token (0.0–1.0 normalised).",
    )
    spread: float = Field(
        description="Bid-ask spread (ask - bid). Tight spread = high confidence.",
    )
    volume_usd: Decimal = Field(
        description="Total traded volume in USD.",
    )
    open_interest_usd: Decimal = Field(
        description="Total open interest in USD — primary conviction weight.",
    )
    capital_conviction_score: float = Field(
        description=(
            "0.0–1.0 score scaling probability by liquidity, spread, and OI. "
            "Below 0.3 = statistical noise. Above 0.7 = hard macro signal."
        ),
    )
    end_date: str = Field(
        default="",
        description="ISO-8601 market resolution date, if known.",
    )
    status: MarketStatus = Field(
        default=MarketStatus.OK,
        description="Data quality flag: OK | DEGRADED | ILLIQUID | STALE.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Snapshot timestamp (UTC).",
    )


class OrderBookSnapshot(BaseModel, frozen=True):
    """
    Raw order book snapshot for a single market.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Intermediate schema before BBO midpoint calculation.
    """

    market_id: str = Field(description="Platform-specific market identifier.")
    platform: Platform = Field(description="Source platform.")
    bids: list[list[float]] = Field(
        description="List of [price, size] bid levels (YES token).",
    )
    asks: list[list[float]] = Field(
        description="List of [price, size] ask levels (YES token).",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Snapshot timestamp (UTC).",
    )


class FedRateMeeting(BaseModel, frozen=True):
    """
    Single FOMC meeting probability data point.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Used by ``get_fed_rate_implied_path`` to construct the
    probability-weighted term structure.
    """

    meeting_date: str = Field(
        description="ISO-8601 date of the FOMC meeting.",
    )
    cut_probability: float = Field(
        description="Probability of a rate CUT at this meeting (0.0–1.0).",
    )
    hold_probability: float = Field(
        description="Probability of HOLD at this meeting (0.0–1.0).",
    )
    hike_probability: float = Field(
        description="Probability of a rate HIKE at this meeting (0.0–1.0).",
    )
    capital_conviction_score: float = Field(
        description="Capital-weighted conviction for the dominant outcome.",
    )
    source_markets: list[str] = Field(
        default_factory=list,
        description="Market IDs contributing to this data point.",
    )
    status: MarketStatus = Field(
        default=MarketStatus.OK,
        description="Data quality flag.",
    )


class FedRateTermStructure(BaseModel, frozen=True):
    """
    Aggregated FOMC rate path term structure.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Consumed by ``MacroCrossMarketAgent`` for forward-looking rate expectations.
    """

    meetings: list[FedRateMeeting] = Field(
        description="Chronologically ordered FOMC meeting probabilities.",
    )
    implied_cuts_12m: float = Field(
        description="Expected number of 25bp cuts in next 12 months.",
    )
    dominant_path: str = Field(
        description="'easing', 'tightening', or 'hold' — overall bias.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC).",
    )
    status: MarketStatus = Field(
        default=MarketStatus.OK,
        description="Overall data quality.",
    )


class RegulatoryEvent(BaseModel, frozen=True):
    """
    Single regulatory event probability for the risk matrix.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    """

    market_id: str = Field(description="Platform market identifier.")
    platform: Platform = Field(description="Source platform.")
    question: str = Field(description="Regulatory event description.")
    agency: str = Field(
        description="Regulatory body: 'SEC', 'CFTC', 'Fed', 'Congress', 'other'.",
    )
    probability: float = Field(
        description="BBO midpoint probability (0.0–1.0).",
    )
    capital_conviction_score: float = Field(
        description="Capital-weighted conviction score.",
    )
    impact_direction: str = Field(
        default="neutral",
        description="'bullish', 'bearish', or 'neutral' for crypto markets.",
    )
    open_interest_usd: Decimal = Field(
        description="Total OI in USD.",
    )
    status: MarketStatus = Field(
        default=MarketStatus.OK,
        description="Data quality flag.",
    )


class RegulatoryRiskMatrix(BaseModel, frozen=True):
    """
    Aggregated regulatory risk matrix.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    Consumed by ``MacroCrossMarketAgent`` for regulatory-scenario weighting.
    """

    events: list[RegulatoryEvent] = Field(
        description="All active regulatory markets sorted by conviction.",
    )
    net_regulatory_bias: float = Field(
        description=(
            "-1.0 to +1.0: negative = bearish regulatory outlook, "
            "positive = bullish / favourable."
        ),
    )
    high_conviction_count: int = Field(
        description="Number of events with capital_conviction_score > 0.7.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC).",
    )
    status: MarketStatus = Field(
        default=MarketStatus.OK,
        description="Overall matrix health.",
    )
