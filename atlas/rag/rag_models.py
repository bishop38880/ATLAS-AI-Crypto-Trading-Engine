"""RAG Pipeline data models — MemoryDocument and RAGTradeOutcome.

S3-P5 canonical models for the persistent memory layer. All models are
frozen Pydantic v2 with Decimal financial fields.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RAGTradeOutcome(BaseModel):
    """Trade outcome payload enriching a stored signal memory.

    Attributes:
        signal_id: Canonical signal correlation key.
        entry_price: Trade entry price (Decimal precision).
        exit_price: Trade exit price if closed (None if still open).
        exit_reason: Human-readable reason for closure.
        pnl_pct: Percentage PnL as dimensionless ratio (float OK).
        duration_seconds: Wall-clock duration of the position.
        market_moved_as: Did the market move as predicted?
    """

    model_config = ConfigDict(frozen=True)

    signal_id: str = Field(min_length=1)
    entry_price: Decimal
    exit_price: Decimal | None = None
    exit_reason: str | None = None
    pnl_pct: float | None = None
    duration_seconds: int | None = None
    market_moved_as: Literal[
        "PREDICTED", "AGAINST", "FLAT"
    ] | None = None


class MemoryDocument(BaseModel):
    """Canonical document stored in the RAG vector pipeline.

    Attributes:
        document_id: UUID primary key.
        asset: Trading pair (e.g. ``BTCUSDT``).
        cycle_timestamp: UTC time of the analysis cycle.
        signal_score: Normalised confluence score (0–100).
        signal_decision: Decision string (e.g. ``Strong Buy``).
        category_scores: Per-category score ratios (float OK).
        agent_explanations: Free-text explanations from agents.
        outcome: Trade outcome if resolved, else None.
        text_content: Human-readable summary used for embedding.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str
    asset: str
    cycle_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    signal_score: int = 0
    signal_decision: str = "Hold"
    category_scores: dict[str, float] = Field(default_factory=dict)
    agent_explanations: list[str] = Field(default_factory=list)
    outcome: RAGTradeOutcome | None = None
    text_content: str = ""
