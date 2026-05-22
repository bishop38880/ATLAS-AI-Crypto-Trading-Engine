"""Pydantic contracts for POLARIS dashboard intelligence streaming (ATLAS scope).

All payloads are synthesis, scoring, and RAG context only. Execution, sizing,
and simulated fills are explicitly out of contract.
"""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, Field, computed_field

# ── Schema versioning (single source for Redis channels + payloads) ─────────

CONFLUENCE_ENGINE_VERSION: Final[str] = "2.3"
IntelligenceSchemaVersion = Literal["2.3"]
CURRENT_INTELLIGENCE_SCHEMA: Final[IntelligenceSchemaVersion] = "2.3"


def build_intelligence_pubsub_channel(schema_version: IntelligenceSchemaVersion) -> str:
    """Deterministic Redis pub/sub channel for dashboard intelligence (writer side)."""
    return "polaris:dashboard:atlas:intelligence:v{}".format(schema_version.replace(".", "_"))


def build_intelligence_snapshot_channel(schema_version: IntelligenceSchemaVersion) -> str:
    """Redis channel key pattern for last-known snapshot handle (auxiliary, not pub/sub)."""
    return "polaris:dashboard:atlas:intelligence:snapshot:v{}".format(
        schema_version.replace(".", "_"),
    )


class IntelligenceRedisChannels(BaseModel, frozen=True):
    """Auto-derived Redis destinations from the active intelligence schema version."""

    schema_version: IntelligenceSchemaVersion = Field(default=CURRENT_INTELLIGENCE_SCHEMA)

    @computed_field
    def pubsub_stream(self) -> str:
        return build_intelligence_pubsub_channel(self.schema_version)

    @computed_field
    def snapshot_key(self) -> str:
        return build_intelligence_snapshot_channel(self.schema_version)


# ── Intelligence payloads ───────────────────────────────────────────────────


class HistoricalSignalMetadataV23(BaseModel, frozen=True):
    """Historical signal metadata slice (no actions, sizes, or execution state)."""

    schema_version: IntelligenceSchemaVersion = Field(default=CURRENT_INTELLIGENCE_SCHEMA)
    signal_id: str = Field(
        min_length=1,
        description="Canonical ATLAS signal correlation id when present.",
    )
    asset: str = Field(min_length=1, description="Wire-format asset / pair key.")
    timeframe: str = Field(default="30m")
    timestamp_millis: int = Field(
        ge=0,
        description="UTC signal time in milliseconds since epoch.",
    )
    normalized_score_0_100: int = Field(ge=0, le=100)
    raw_confluence_reference_0_220: int = Field(
        ge=0,
        le=220,
        description="Reference 220-scale score from upstream pipeline (read-only).",
    )
    key_convergences: tuple[str, ...] = Field(default_factory=tuple)
    key_risks: tuple[str, ...] = Field(default_factory=tuple)
    redis_source_key: str | None = Field(
        default=None,
        description="Redis KV key the snapshot was loaded from (diagnostics).",
    )


class RagRetrievalChunkV23(BaseModel, frozen=True):
    """One retrieved memory row for dashboard explanation (RAG context)."""

    document_id: str = Field(min_length=1)
    similarity_score: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(
        default=0.0,
        ge=0.0,
        description="Freshness-adjusted ranking score when enabled upstream.",
    )
    timestamp_millis: int = Field(ge=0)
    text_excerpt: str = Field(
        default="",
        description="Short excerpt from payload for UI (not full archival blob).",
    )
    backend: Literal["qdrant", "lancedb", "empty"] = "qdrant"


class RagContextBundleV23(BaseModel, frozen=True):
    """Grouped RAG retrieval outcome."""

    query: str = Field(min_length=1)
    keyword_filter: str | None = None
    chunks: tuple[RagRetrievalChunkV23, ...] = Field(default_factory=tuple)


class ConfluenceCategoryBreakdownV23(BaseModel, frozen=True):
    """Integer-only pillar with audit factors."""

    name: Literal["derivatives", "onchain", "technical", "sentiment", "market_context"]
    points: int = Field(ge=0, description="Pillar points after v2.3 cap enforcement.")
    factors: tuple[str, ...] = Field(
        description="Human-readable ledger lines for deterministic scoring.",
    )


class ConfluenceScoreBundleV23(BaseModel, frozen=True):
    """ConfluenceScoringEngine v2.3 aggregate."""

    schema_version: IntelligenceSchemaVersion = Field(default=CURRENT_INTELLIGENCE_SCHEMA)
    total_points: int = Field(ge=0, le=220)
    categories: tuple[ConfluenceCategoryBreakdownV23, ...]
    strength_label: Literal["STRONG", "MODERATE", "WEAK", "NO_SIGNAL"] = Field(
        description="Informational only — not a trade instruction.",
    )
    factors: tuple[str, ...] = Field(
        description="Flattened factor stream mirroring category ledgers.",
    )


class AtlasDashboardIntelligencePayloadV23(BaseModel, frozen=True):
    """Unified dashboard packet: historical metadata + score + RAG context."""

    schema_version: IntelligenceSchemaVersion = Field(default=CURRENT_INTELLIGENCE_SCHEMA)
    generated_at_millis: int = Field(ge=0)
    asset: str = Field(min_length=1)
    historical: HistoricalSignalMetadataV23
    confluence: ConfluenceScoreBundleV23
    rag: RagContextBundleV23
    pipeline_notes: tuple[str, ...] = Field(
        default_factory=tuple,
        description="Non-actionable status hints (degraded paths, empty caches).",
    )


class ConfluenceV23PipelineInput(BaseModel, frozen=True):
    """Pre-capped raw integer ladder per pillar (sum before pillar clamps)."""

    derivatives_raw: int = Field(ge=0, default=0)
    onchain_raw: int = Field(ge=0, default=0)
    technical_raw: int = Field(ge=0, default=0)
    sentiment_raw: int = Field(ge=0, default=0)
    market_context_raw: int = Field(ge=0, default=0)
    sentiment_gate_active: bool = Field(
        default=True,
        description="When False, sentiment pillar is forced to 0 with explicit factors.",
    )


__all__ = [
    "CONFLUENCE_ENGINE_VERSION",
    "CURRENT_INTELLIGENCE_SCHEMA",
    "IntelligenceSchemaVersion",
    "IntelligenceRedisChannels",
    "AtlasDashboardIntelligencePayloadV23",
    "ConfluenceCategoryBreakdownV23",
    "ConfluenceScoreBundleV23",
    "ConfluenceV23PipelineInput",
    "HistoricalSignalMetadataV23",
    "RagContextBundleV23",
    "RagRetrievalChunkV23",
    "build_intelligence_pubsub_channel",
    "build_intelligence_snapshot_channel",
]
