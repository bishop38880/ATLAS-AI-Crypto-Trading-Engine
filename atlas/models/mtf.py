"""
Multi-timeframe confluence models for POLARIS ATLAS.
All scores are per-timeframe ATLAS compact v2.1 scores (0–220 each).
The weighted average is also bounded 0–220.
Decimal is used throughout — no float in financial fields.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TimeframeLabel = Literal["15m", "30m", "4h"]
AlignmentLabel = Literal["BUILDING", "FADING", "FLAT", "RECOVERING"]
MTFDecision = Literal[
    "STRONG_BUY",
    "BUY",
    "WEAK_BUY",
    "STRONG_SELL",
    "SELL",
    "WEAK_SELL",
    "NO_TRADE",
    "STALE_SIGNAL",
]


class TimeframeScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    tf: TimeframeLabel
    score: int = Field(description="Raw ATLAS confluence score, 0–220")
    timestamp: str = Field(description="ISO format — used for staleness check")
    stale_min: int = Field(description="Max age in minutes before rejection")

    @field_validator("score")
    @classmethod
    def score_bounded(cls, v: int) -> int:
        if not 0 <= v <= 220:
            raise ValueError("Score {} out of range 0–220".format(v))
        return v


class MTFBlock(BaseModel):
    """
    Pre-computed multi-timeframe confluence block.
    Attached to signal packets before LLM dispatch.
    All arithmetic is done in Python/Decimal — not by the LLM.
    """

    model_config = ConfigDict(frozen=True)

    sc_4h: int = Field(description="Raw 4h score")
    sc_30m: int = Field(description="Raw 30m score")
    sc_15m: int = Field(description="Raw 15m score")
    weights: tuple[Decimal, Decimal, Decimal] = Field(
        description="(4h, 30m, 15m), must sum to 1.0",
    )
    base_avg: int = Field(description="Weighted avg before alignment multiplier")
    alignment: AlignmentLabel
    multiplier: Decimal = Field(description="Alignment multiplier applied")
    avg: int = Field(description="Final adjusted avg — what the LLM reads")
    stale_tf: TimeframeLabel | None = Field(
        default=None,
        description="Set if any tf is stale",
    )
    is_stale: bool = False


class MTFSignalPacket(BaseModel):
    """
    Wrapper that attaches an MTFBlock to an existing compact v2.1 package.
    The underlying package is stored as raw dict to avoid re-validating
    the full compact schema here.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    mode: Literal["CONFLUENCE", "HYDRA_HUNT"] = "CONFLUENCE"
    mtf: MTFBlock
    package: dict[str, Any] = Field(description="Compact v2.1 signal package (15m entry tf)")
    decision: MTFDecision | None = Field(
        default=None,
        description="Filled after LLM response",
    )
