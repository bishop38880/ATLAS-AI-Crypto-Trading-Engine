"""Reconciliation models — frozen Pydantic v2.

Discrepancy types, individual discrepancy records, and the per-run
reconciliation report.  All financial fields use ``Decimal``.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class DiscrepancyType(str, Enum):
    POSITION_MISMATCH = "POSITION_MISMATCH"      # OMS has position, Bitget doesn't (or wrong side)
    PHANTOM_POSITION = "PHANTOM_POSITION"         # Bitget has position, OMS doesn't know
    SIZE_DIVERGENCE = "SIZE_DIVERGENCE"            # Entry-notional sizes differ > tolerance
    SIDE_MISMATCH = "SIDE_MISMATCH"               # OMS says long, Bitget says short
    STOP_MISSING = "STOP_MISSING"                 # Stop ladder tier missing on Bitget
    MARGIN_MODE_WRONG = "MARGIN_MODE_WRONG"        # Cross margin detected (prohibited)
    OMS_REDIS_DIVERGENCE = "OMS_REDIS_DIVERGENCE"  # Postgres OMS ↔ Redis executor mirror mismatch


class ReconciliationDiscrepancy(BaseModel, frozen=True):
    """A single detected discrepancy between OMS and Bitget state."""

    asset: str
    discrepancy_type: DiscrepancyType
    oms_value: str                       # human-readable OMS state
    bitget_value: str                    # human-readable Bitget state
    severity: Literal["low", "medium", "high", "critical"]
    auto_action_taken: str | None
    requires_human: bool
    detected_at: datetime


class ReconciliationReport(BaseModel, frozen=True):
    """Summary of a single reconciliation run."""

    run_at: datetime
    duration_ms: int
    oms_positions_checked: int
    bitget_positions_found: int
    discrepancies: list[ReconciliationDiscrepancy]
    all_clear: bool
    halt_triggered: bool
