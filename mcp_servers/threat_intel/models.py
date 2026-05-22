"""
Pydantic v2 frozen schemas for Pre-Execution Threat Intel MCP.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Provides strict immutable DTOs for Tenderly simulation results,
GoPlus token security reports, Shadow Delta calculations, and
the master ExecutionSafetyReport.

Sentinel Invariants:
  - All models frozen=True (immutable DTOs)
  - Wei values typed as str to prevent JSON precision loss
  - Tax rates as Decimal for financial precision
  - Dimensionless percentages (delta_pct) as float
  - Every field has Field(description=...)
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────


class ThreatLevel(str, Enum):
    """Overall threat classification for the execution."""

    SAFE = "SAFE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class DataStatus(str, Enum):
    """Operational status of an API data point."""

    OK = "OK"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


# ──────────────────────────────────────────────────────────────
# Tenderly Simulation Models
# ──────────────────────────────────────────────────────────────


class AssetChange(BaseModel, frozen=True):
    """
    Single asset state change from a Tenderly simulation.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Represents one token transfer or ETH movement observed during
    the shadow execution. The ``amount`` field is a string to
    preserve exact wei precision in JSON transport.
    """

    token_address: str = Field(
        description="Contract address of the transferred token (0x0 for native).",
    )
    token_name: str = Field(
        default="",
        description="Human-readable token name.",
    )
    token_symbol: str = Field(
        default="",
        description="Token ticker symbol.",
    )
    token_decimals: int = Field(
        default=18,
        description="Token decimal places.",
    )
    from_address: str = Field(
        description="Address sending the asset.",
    )
    to_address: str = Field(
        description="Address receiving the asset.",
    )
    amount: str = Field(
        description="Transfer amount in raw wei (string to prevent precision loss).",
    )
    change_type: str = Field(
        default="transfer",
        description="Type of change: 'transfer', 'mint', 'burn'.",
    )


class TenderlySimulationResult(BaseModel, frozen=True):
    """
    Full result from a Tenderly shadow execution.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    If ``success`` is False, the transaction would revert on-chain.
    Even if ``success`` is True, the agent MUST check ``asset_changes``
    for hidden tax traps via the Shadow Delta calculation.
    """

    success: bool = Field(
        description="True if the simulated transaction did NOT revert.",
    )
    gas_used: int = Field(
        default=0,
        description="Gas consumed by the simulated execution.",
    )
    asset_changes: list[AssetChange] = Field(
        default_factory=list,
        description="All asset state changes observed during simulation.",
    )
    error_message: str = Field(
        default="",
        description="Revert reason or error string if simulation failed.",
    )
    status: DataStatus = Field(
        default=DataStatus.OK,
        description="API call health status.",
    )
    raw_response_hash: str = Field(
        default="",
        description="Hash of raw response for audit trail.",
    )


# ──────────────────────────────────────────────────────────────
# GoPlus Security Models
# ──────────────────────────────────────────────────────────────


class TokenSecurityFlags(BaseModel, frozen=True):
    """
    Parsed GoPlus boolean flags for a token contract.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    If ANY critical flag is True or tax exceeds 10%, the PROMETHEUS
    agent MUST issue a HARD_ABORT and drop the transaction.

    Flag interpretation:
      - is_honeypot: Contract prevents selling (fatal)
      - cannot_sell_all: Max sell amount restriction
      - transfer_pausable: Owner can freeze trading
      - is_blacklisted: Address-level freeze capability
      - is_mintable: Owner can inflate supply
      - buy_tax / sell_tax: Hidden transfer tax (Decimal)
    """

    is_honeypot: bool = Field(
        default=False,
        description="True if the token cannot be sold (fatal honeypot).",
    )
    cannot_sell_all: bool = Field(
        default=False,
        description="True if max sell amount limits exist.",
    )
    transfer_pausable: bool = Field(
        default=False,
        description="True if the owner can freeze all transfers.",
    )
    is_blacklisted: bool = Field(
        default=False,
        description="True if the contract has address blacklisting.",
    )
    is_mintable: bool = Field(
        default=False,
        description="True if the owner can mint unlimited tokens.",
    )
    buy_tax: Decimal = Field(
        default=Decimal("0"),
        description="Buy-side transfer tax as a decimal (0.10 = 10%).",
    )
    sell_tax: Decimal = Field(
        default=Decimal("0"),
        description="Sell-side transfer tax as a decimal (0.10 = 10%).",
    )
    is_open_source: bool = Field(
        default=False,
        description="True if contract source code is verified.",
    )
    is_proxy: bool = Field(
        default=False,
        description="True if contract is a proxy (upgradeable).",
    )
    owner_address: str = Field(
        default="",
        description="Contract owner address (empty if renounced).",
    )


class TokenSecurityReport(BaseModel, frozen=True):
    """
    Full GoPlus security analysis report for a token contract.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Wraps the parsed flags with metadata and risk summary.
    """

    token_address: str = Field(
        description="Analysed token contract address.",
    )
    chain_id: str = Field(
        description="Numeric chain ID where the token resides.",
    )
    flags: TokenSecurityFlags = Field(
        description="Parsed security flags from GoPlus bytecode analysis.",
    )
    risk_summary: str = Field(
        default="",
        description="Human-readable risk summary.",
    )
    status: DataStatus = Field(
        default=DataStatus.OK,
        description="API call health status.",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Analysis timestamp (UTC).",
    )


# ──────────────────────────────────────────────────────────────
# Shadow Delta Models
# ──────────────────────────────────────────────────────────────


class ShadowDeltaResult(BaseModel, frozen=True):
    """
    Shadow Delta calculation output.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Compares expected vs actual token receipt from Tenderly simulation.
    A delta_pct below 90.0 indicates a >10% hidden tax or slippage
    and triggers HARD_ABORT.

    Formula: delta_pct = (actual_wei / expected_wei) * 100
    """

    expected_wei: str = Field(
        description="Expected output in raw wei (from PROMETHEUS trade intent).",
    )
    actual_wei: str = Field(
        description="Actual output observed in Tenderly simulation.",
    )
    delta_pct: float = Field(
        description="Shadow Delta percentage: (actual/expected)*100.",
    )
    is_safe: bool = Field(
        description="True if delta_pct >= threshold (default 90%).",
    )
    threshold_pct: float = Field(
        default=90.0,
        description="Safety threshold percentage.",
    )


# ──────────────────────────────────────────────────────────────
# Master Execution Safety Report
# ──────────────────────────────────────────────────────────────


class ExecutionSafetyReport(BaseModel, frozen=True):
    """
    Master pre-execution safety verdict.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    This is the FINAL output consumed by PROMETHEUS before signing
    any EVM transaction. If ``hard_abort`` is True, PROMETHEUS MUST
    drop the transaction immediately — this is a NON-NEGOTIABLE
    command. The ``threat_reasons`` array provides auditable detail
    for every detected threat.
    """

    hard_abort: bool = Field(
        description=(
            "HARD_ABORT flag. If True, PROMETHEUS MUST drop this "
            "transaction immediately. Non-negotiable."
        ),
    )
    threat_level: ThreatLevel = Field(
        description="Overall threat classification: SAFE, WARNING, CRITICAL.",
    )
    threat_reasons: list[str] = Field(
        default_factory=list,
        description=(
            "Auditable list of detected threats. Examples: "
            "'GoPlus: is_honeypot=True', 'Tenderly: 99% Hidden Tax Detected'."
        ),
    )
    simulation: TenderlySimulationResult | None = Field(
        default=None,
        description="Tenderly simulation result (None if API failed).",
    )
    security_report: TokenSecurityReport | None = Field(
        default=None,
        description="GoPlus security report (None if API failed).",
    )
    shadow_delta: ShadowDeltaResult | None = Field(
        default=None,
        description="Shadow Delta result (None if simulation failed).",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="Report generation timestamp (UTC).",
    )
