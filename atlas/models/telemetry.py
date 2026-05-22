"""TelemetryEvent — pipeline cycle telemetry for observability.

Captures timing and participation metadata for each orchestrator
cycle. Frozen Pydantic model — immutable after construction.

Architecture note:
    This is a minimal telemetry model for Session 00. Future sessions
    (e.g. Langfuse integration) will extend with span IDs, token
    counts, and per-agent latency breakdowns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class TelemetryEvent(BaseModel, frozen=True):
    """Immutable snapshot of a single pipeline cycle's telemetry.

    Attributes:
        cycle_id: Correlation ID for the pipeline cycle.
        cycle_latency_ms: Total orchestrator cycle time in milliseconds.
        agent_count: Number of agents that participated in the cycle.
        timestamp: UTC timestamp when telemetry was captured.
    """

    cycle_id: str = Field(
        description="Correlation ID tying all agent results in one cycle",
    )
    cycle_latency_ms: float = Field(
        ge=0.0,
        description="Total orchestrator cycle time in milliseconds",
    )
    agent_count: int = Field(
        ge=0,
        description="Number of agents that participated",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="UTC timestamp when telemetry was captured",
    )
