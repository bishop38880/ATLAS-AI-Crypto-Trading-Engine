"""Decision provenance models — append-only signal reproducibility records.

Every emitted signal produces exactly one ``ProvenanceRecord``.
These records are never updated or deleted — they exist to answer
"given the inputs at time T, did the code emit the correct signal?"

Architecture:
    - All financial fields are ``Decimal``.
    - Model is ``frozen=True``.
    - Serialised via ``msgspec.json.encode(record.model_dump())``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class AgentVerdict(BaseModel, frozen=True):
    """Snapshot of one agent's output at signal time.

    Attributes:
        agent_name: Agent identifier.
        state: Agent lifecycle state at emission time.
        score: Raw score contributed.
        max_score: Maximum possible score for this agent.
        direction: Directional conviction string.
        veto: Whether the agent vetoed.
    """

    agent_name: str
    state: str = Field(
        description="AgentState value at emission time",
    )
    score: int = Field(ge=0)
    max_score: int = Field(ge=0)
    direction: str
    veto: bool = False


class ProvenanceRecord(BaseModel):
    """Append-only provenance record for a single emitted signal.

    One record per signal, never updated. Provides the full audit
    trail needed to reconstruct why a signal was emitted.

    Attributes:
        signal_id: Matches SignalOutput.signal_id (correlation key).
        timestamp: UTC time the signal was emitted.
        schema_version: SignalOutput schema version at emission.
        git_sha: Git commit SHA of the running code (best-effort).
        input_bundle_sha256: SHA-256 of the serialised input data.
        agent_verdicts: Per-agent scoring snapshot.
        raw_confluence_score: Raw 220-point score.
        normalised_score: Normalised 0–100 score.
        decision: Final signal decision string.
        pipeline_confidence: Pipeline confidence score (0.0–1.0).
        confidence_tier: Confidence tier string.
        signal_output_sha256: SHA-256 of the full serialised SignalOutput.
        cycle_latency_ms: Pipeline cycle duration.
    """

    model_config = ConfigDict(frozen=True)

    signal_id: str
    timestamp: datetime
    schema_version: str
    git_sha: str = Field(
        default="unknown",
        description="Git commit SHA; best-effort, not critical",
    )
    input_bundle_sha256: str = Field(
        description="SHA-256 of the serialised provider input bundle",
    )
    agent_verdicts: list[AgentVerdict]
    raw_confluence_score: int = Field(ge=0, le=220)
    normalised_score: int = Field(ge=0, le=100)
    decision: str
    pipeline_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    confidence_tier: str = Field(default="STANDARD")
    signal_output_sha256: str = Field(
        description="SHA-256 of the full serialised SignalOutput",
    )
    cycle_latency_ms: float = Field(ge=0.0, default=0.0)


def compute_sha256(data: bytes) -> str:
    """Compute SHA-256 hex digest of raw bytes.

    Args:
        data: Raw bytes to hash.

    Returns:
        Lowercase hex digest string.
    """
    return hashlib.sha256(data).hexdigest()
