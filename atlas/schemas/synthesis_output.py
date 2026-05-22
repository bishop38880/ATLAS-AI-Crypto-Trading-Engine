"""
Canonical frozen schema for DeepSeek LLM synthesis layer output.
Per POLARIS Strategy Document v1.1 §2, §4, and §8.
Every trade decision passes through this schema.
The schema is the contract between ATLAS synthesis and PROMETHEUS execution.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Archetype(str, Enum):
    """Five canonical signal archetypes per Strategy Doc v1.1 §4."""

    LIQUIDITY_TRAP = "LIQUIDITY_TRAP"
    CAPITULATION_REVERSAL = "CAPITULATION_REVERSAL"
    DEAD_ZONE = "DEAD_ZONE"
    DERIVATIVES_SKEW = "DERIVATIVES_SKEW"
    CONTRADICTION = "CONTRADICTION"


class TradeDecision(str, Enum):
    """Trade decision labels emitted by the synthesis layer."""

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    WEAK_BUY = "WEAK_BUY"
    STRONG_SELL = "STRONG_SELL"
    SELL = "SELL"
    WEAK_SELL = "WEAK_SELL"
    WATCH = "WATCH"
    NO_TRADE = "NO_TRADE"
    BLOCK = "BLOCK"


class DimensionBreakdown(BaseModel):
    """
    Per-dimension integer scores. Must sum to confluence_total.
    Each value must not exceed its dimension maximum per Strategy Doc v1.1 §2.1.
    Maxima: derivatives=75, whale=65, sentiment=35, macro=30, technical=15.
    """

    model_config = ConfigDict(frozen=True)

    derivatives: int
    whale: int
    sentiment: int
    macro: int
    technical: int

    @model_validator(mode="after")
    def validate_dimension_ceilings(self) -> DimensionBreakdown:
        if self.derivatives > 75:
            raise ValueError("Derivatives dimension exceeds maximum 75")
        if self.whale > 65:
            raise ValueError("Whale dimension exceeds maximum 65")
        if self.sentiment > 35:
            raise ValueError("Sentiment dimension exceeds maximum 35")
        if self.sentiment < -35:
            raise ValueError("Sentiment dimension below minimum -35")
        if self.macro > 30:
            raise ValueError("Macro dimension exceeds maximum 30")
        if self.technical > 15:
            raise ValueError("Technical dimension exceeds maximum 15")
        return self


class AgentProvenance(BaseModel):
    """Source attribution for each dimension score."""

    model_config = ConfigDict(frozen=True)

    derivatives_agent: str
    whale_agent: str
    sentiment_agent: str
    macro_agent: str
    technical_agent: str
    synthesis_model: str


class SynthesisOutput(BaseModel):
    """
    Canonical output schema for the DeepSeek LLM synthesis layer.
    Frozen — immutable after construction.
    All score fields are integers per the 220-pt integer-only system.
    """

    model_config = ConfigDict(frozen=True)

    confluence_total: Annotated[int, Field(ge=0, le=220)]
    dimension_breakdown: DimensionBreakdown
    archetype: Archetype
    decision: TradeDecision
    direction: Annotated[str, Field(pattern=r"^(long|short|none)$")]
    confidence_score: Annotated[float, Field(ge=0.0, le=1.0)]
    active_flags: list[str]
    provenance: AgentProvenance
    latency_ms: int
    data_degraded: bool = False

    @model_validator(mode="after")
    def validate_score_consistency(self) -> SynthesisOutput:
        breakdown = self.dimension_breakdown
        expected = (
            breakdown.derivatives
            + breakdown.whale
            + breakdown.sentiment
            + breakdown.macro
            + breakdown.technical
        )
        if self.confluence_total != expected:
            raise ValueError(
                "confluence_total {} != dimension sum {}".format(
                    self.confluence_total,
                    expected,
                ),
            )
        return self

    @model_validator(mode="after")
    def validate_contradiction_no_trade(self) -> SynthesisOutput:
        if self.archetype == Archetype.CONTRADICTION and self.decision != TradeDecision.NO_TRADE:
            raise ValueError("CONTRADICTION archetype must produce NO_TRADE decision")
        return self

    @model_validator(mode="after")
    def validate_liquidity_trap_block(self) -> SynthesisOutput:
        if self.archetype == Archetype.LIQUIDITY_TRAP and self.decision != TradeDecision.BLOCK:
            raise ValueError("LIQUIDITY_TRAP archetype must produce BLOCK decision")
        return self

    @model_validator(mode="after")
    def validate_low_confidence_watch(self) -> SynthesisOutput:
        if self.confidence_score < 0.5 and self.decision not in (
            TradeDecision.WATCH,
            TradeDecision.NO_TRADE,
        ):
            raise ValueError(
                "Low confidence ({}) must produce WATCH or NO_TRADE".format(
                    self.confidence_score,
                ),
            )
        return self

    @model_validator(mode="after")
    def validate_data_degraded_watch(self) -> SynthesisOutput:
        if self.data_degraded and self.confluence_total > 139:
            raise ValueError(
                "DATA_DEGRADED flag requires confluence_total <= 139 (WATCH band)",
            )
        return self


def build_fallback_synthesis_output(
    *,
    symbol: str = "",
    synthesis_model: str = "unknown",
    latency_ms: int = 0,
    failure_flag: str = "SYNTHESIS_PARSE_FAILURE",
) -> SynthesisOutput:
    """Safe NO_TRADE payload when LLM output cannot be validated."""
    _ = symbol
    provenance = AgentProvenance(
        derivatives_agent="unknown",
        whale_agent="unknown",
        sentiment_agent="unknown",
        macro_agent="unknown",
        technical_agent="unknown",
        synthesis_model=synthesis_model,
    )
    return SynthesisOutput(
        confluence_total=0,
        dimension_breakdown=DimensionBreakdown(
            derivatives=0,
            whale=0,
            sentiment=0,
            macro=0,
            technical=0,
        ),
        archetype=Archetype.DEAD_ZONE,
        decision=TradeDecision.NO_TRADE,
        direction="none",
        confidence_score=0.0,
        active_flags=[failure_flag],
        provenance=provenance,
        latency_ms=latency_ms,
    )
