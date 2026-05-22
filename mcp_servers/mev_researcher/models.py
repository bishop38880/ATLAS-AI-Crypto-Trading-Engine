"""Pydantic schemas for mempool surveillance."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PendingMempoolEvent(BaseModel, frozen=True):
    """Unified pending observation across EVM and Solana pipelines."""

    chain: Literal["ethereum", "solana"] = Field(description="Observation chain.")
    pool_address: str = Field(description="Target pool/program address (checksummed or base58).")
    tx_signature: str = Field(description="EVM tx hash hex or Solana signature.")
    direction_bias: Literal["buy", "sell", "neutral"] = Field(description="Directed flow heuristic.")
    notional_usd: Decimal = Field(description="Estimated USD directional notional.")
    priority_fee_micros: Decimal = Field(
        description="Priority fee heuristic (micros USD or Gwei‑scaled surrogate).",
    )
    complexity_score: Decimal = Field(
        description="Calldata/account complexity (0‑1 heuristic).",
    )
    captured_at_unix_ms: int = Field(description="Capture time millis since Unix epoch.")

    liquidity_removal_candidate: bool = Field(
        default=False,
        description="Whether calldata/logs hint at liquidity removal/admin action.",
    )


class ToxicityReport(BaseModel, frozen=True):
    """Real-time mempool toxicity envelope for PROMETHEUS routing."""

    chain: Literal["ethereum", "solana"] = Field(description="Reporting chain.")
    pool_address: str = Field(description="Pool identifier.")
    mempool_toxicity_index: Decimal = Field(
        ge=Decimal("0"),
        le=Decimal("1"),
        description="Composite MTI ∈ [0,1]. Higher = more hostile mempool.",
    )
    pending_volume_usd: Decimal = Field(description="Net directional pending USD in window.")
    threat_types: list[str] = Field(
        description="Enumerated heuristic tags (sandwich_pressure, searcher_spike, etc.).",
    )
    degraded: bool = Field(
        default=False,
        description="Whether feed is offline/unavailable and values are degraded.",
    )
    degraded_reason: str = Field(default="", description="Reason when degraded.")


class ExecutionRecommendation(BaseModel, frozen=True):
    """Deterministic sizing guardrail."""

    recommended_strategy: Literal["VWAP", "ICEBERG", "PROTECTIVE_SWARM", "ABORT"] = Field(
        description="Strategy label for PROMETHEUS routers.",
    )
    rationale: str = Field(description="Human/agent readable justification.")
    mti: Decimal = Field(description="MTI at evaluation time.")

    simulated_sandwich_hazard: bool = Field(
        description="Heuristic sandwich exposure for sized trade.",
    )


class RugScanResult(BaseModel, frozen=True):
    """Liquidity drain sentinel."""

    drain_detected: bool = Field(description="Whether removals look imminent.")
    chain: Literal["ethereum", "solana"] = Field(description="Reporting chain.")
    pool_address: str = Field(description="Pool identifier.")
