"""Strict msgspec.Struct models for the Token Unlocks MCP.

ATLAS Intelligence Layer — NewsMacroAgent Integration.

All token quantities, supply figures, and percentages use ``decimal.Decimal``
to prevent floating-point precision drift on large token supplies.  Standard
``float`` and bare ``dict`` are strictly banned for financial values.

The custom ``decimal_enc_hook`` / ``decimal_dec_hook`` pair configures
``msgspec`` to natively round-trip ``Decimal`` through JSON as strings,
preserving full precision across the wire.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import msgspec


# ───────────────────────────── Decimal Hooks ─────────────────────────────────

def decimal_enc_hook(obj: Any) -> Any:
    """Encode ``Decimal`` as a string to preserve precision in JSON.

    Called by ``msgspec.json.Encoder`` for types it does not handle
    natively.  Returns the string representation so the downstream
    consumer can reconstruct the exact ``Decimal`` value.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"Unsupported type: {type(obj)}")


def decimal_dec_hook(type_: type, obj: Any) -> Any:
    """Decode a JSON string or number into ``Decimal``.

    Handles both ``"123.456"`` (string) and ``123.456`` (float/int)
    inputs that may arrive from DefiLlama or Dune payloads.
    """
    if type_ is Decimal:
        return Decimal(str(obj))
    raise TypeError(f"Unsupported type: {type_}")


# Pre-built encoder / decoder singletons — reuse everywhere.
ENCODER = msgspec.json.Encoder(enc_hook=decimal_enc_hook)
DECODER = msgspec.json.Decoder(dec_hook=decimal_dec_hook)


# ─────────────────────── Layer 1: DefiLlama Models ──────────────────────────

class DefiLlamaSupply(msgspec.Struct, frozen=True):
    """Tokenomics snapshot from DefiLlama free endpoints.

    Used by ``get_supply_ratios`` to deliver circulating vs. total supply
    data to the ATLAS Intelligence Layer — NewsMacroAgent.

    Attributes:
        symbol: Uppercase ticker (e.g. ``"SOL"``).
        circulating_supply: Current tokens in active circulation.
        total_supply: Maximum / total token supply.
        circulating_ratio: ``circulating / total`` as a Decimal (0–1).
        price_usd: Latest spot price in USD.
        market_cap_usd: Circulating supply × price.
        fetched_at: UTC timestamp of the fetch.
        status: ``"OK"`` or ``"DEGRADED"`` if the fetch partially failed.
    """

    symbol: str
    circulating_supply: Decimal = Decimal("0")
    total_supply: Decimal = Decimal("0")
    circulating_ratio: Decimal = Decimal("0")
    price_usd: Decimal = Decimal("0")
    market_cap_usd: Decimal = Decimal("0")
    fetched_at: str = ""
    status: str = "OK"


# ─────────────────────── Layer 2: Dune Models ───────────────────────────────

class DuneUnlockEvent(msgspec.Struct, frozen=True):
    """A single on-chain vesting unlock event from Dune Analytics.

    Represents one row from a Dune query that tracks token vesting
    contract movements.  Feeds the Exit Liquidity Guardrail inside the
    NewsMacroAgent.

    Attributes:
        symbol: Uppercase ticker.
        unlock_amount: Absolute token count scheduled for release.
        unlock_usd_value: Estimated USD value at current price.
        unlock_datetime: Exact UTC datetime of the unlock.
        hours_until_unlock: Hours remaining until the event fires.
        unlock_type: ``"CLIFF"``, ``"LINEAR"``, or ``"UNKNOWN"``.
        beneficiary: On-chain address or label of the recipient.
    """

    symbol: str
    unlock_amount: Decimal = Decimal("0")
    unlock_usd_value: Decimal = Decimal("0")
    unlock_datetime: str = ""
    hours_until_unlock: Decimal = Decimal("0")
    unlock_type: str = "UNKNOWN"
    beneficiary: str = ""


class DuneUnlockSchedule(msgspec.Struct, frozen=True):
    """Normalised vesting schedule for an asset from Dune.

    Groups all imminent unlock events and provides aggregate metrics
    consumed by the risk engine.

    Attributes:
        symbol: Uppercase ticker.
        events: List of individual unlock events.
        total_unlock_amount: Sum of all ``unlock_amount`` values.
        total_unlock_usd: Sum of all ``unlock_usd_value`` values.
        query_id: Dune query ID that produced these results.
        execution_id: Dune execution ID for audit trail.
        fetched_at: UTC timestamp.
        status: ``"OK"`` or ``"DEGRADED"``.
    """

    symbol: str
    events: list[DuneUnlockEvent] = msgspec.field(default_factory=list)
    total_unlock_amount: Decimal = Decimal("0")
    total_unlock_usd: Decimal = Decimal("0")
    query_id: int = 0
    execution_id: str = ""
    fetched_at: str = ""
    status: str = "OK"


# ──────────────────── Aggregated Risk Report ────────────────────────────────

class UnlockRiskReport(msgspec.Struct, frozen=True):
    """Master exit-liquidity risk report for the NewsMacroAgent.

    This is the definitive output of ``evaluate_exit_liquidity_risk``.
    The ``conviction_suppression`` flag, when ``True``, instructs the
    ATLAS scoring pipeline to veto any pending long positions for
    this asset, preventing the system from being used as exit liquidity
    by VC and team token unlocks.

    **Exit Liquidity Guardrail:**
    If ``impact_pct > threshold_pct`` AND any unlock fires within
    72 hours, ``conviction_suppression = True``.

    Attributes:
        symbol: Uppercase ticker.
        conviction_suppression: ``True`` → veto longs for this asset.
        impact_pct: ``(unlock_amount / circulating_supply) * 100``.
        threshold_pct: The configured suppression threshold.
        circulating_supply: From DefiLlama Layer 1 data.
        total_unlock_amount_72h: Tokens unlocking within 72 hours.
        unlock_events_72h: Count of events within the window.
        risk_tier: ``"CRITICAL"``, ``"ELEVATED"``, ``"NORMAL"``.
        supply_data: Full DefiLlama supply snapshot.
        unlock_schedule: Full Dune unlock schedule.
        evaluated_at: UTC timestamp of the evaluation.
    """

    symbol: str
    conviction_suppression: bool = False
    impact_pct: Decimal = Decimal("0")
    threshold_pct: Decimal = Decimal("2.0")
    circulating_supply: Decimal = Decimal("0")
    total_unlock_amount_72h: Decimal = Decimal("0")
    unlock_events_72h: int = 0
    risk_tier: str = "NORMAL"
    supply_data: DefiLlamaSupply | None = None
    unlock_schedule: DuneUnlockSchedule | None = None
    evaluated_at: str = ""
