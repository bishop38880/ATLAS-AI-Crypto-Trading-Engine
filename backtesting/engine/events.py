"""Replay event log models for tutorial and debugging."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReplayEvent(BaseModel, frozen=True):
    """Single event in the replay event log."""

    bar_index: int
    timestamp_utc: str
    asset: str
    event_type: str = Field(
        description="SCORE, ENTRY, EXIT, SKIP, or BLOCKED",
    )
    score: int | None = None
    signal_class: str | None = None
    message: str = Field(description="Human-readable description.")
    trade_id: str | None = None
