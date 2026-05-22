"""CRA tax / ACB data models — frozen Pydantic v2.

Adjusted Cost Base lots, dispositions, and T2125 summary for
Canadian self-employment tax reporting.

All financial fields use ``Decimal``.  No ``float`` contamination.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class AcbLot(BaseModel, frozen=True):
    """A single acquisition lot contributing to the ACB pool."""

    lot_id: str = Field(description="Unique lot identifier")
    asset: str = Field(description="Crypto asset, e.g. BTC")
    quantity: Decimal = Field(description="Quantity acquired")
    cost_usd: Decimal = Field(description="Total cost in USD")
    cost_cad: Decimal = Field(description="Total cost in CAD")
    exchange_rate: Decimal = Field(
        description="USD/CAD rate at acquisition time",
    )
    acquired_at: datetime = Field(description="Acquisition timestamp")


class Disposition(BaseModel, frozen=True):
    """A taxable disposition event."""

    disposition_id: str = Field(description="Unique disposition ID")
    asset: str = Field(description="Crypto asset disposed")
    quantity: Decimal = Field(description="Quantity disposed")
    proceeds_usd: Decimal = Field(description="Sale proceeds in USD")
    proceeds_cad: Decimal = Field(description="Sale proceeds in CAD")
    acb_per_unit_cad: Decimal = Field(
        description="ACB per unit at time of disposition (CAD)",
    )
    total_acb_cad: Decimal = Field(
        description="Total ACB for this disposition (qty * acb_per_unit)",
    )
    gain_loss_cad: Decimal = Field(
        description="Capital gain (positive) or loss (negative) in CAD",
    )
    exchange_rate: Decimal = Field(
        description="USD/CAD rate at disposition time",
    )
    disposed_at: datetime = Field(description="Disposition timestamp")


class T2125Summary(BaseModel, frozen=True):
    """T2125 self-employment income summary for a tax year."""

    tax_year: int = Field(description="Calendar year")
    total_dispositions: int = Field(
        description="Number of taxable dispositions",
    )
    gross_proceeds_cad: Decimal = Field(
        description="Total gross proceeds in CAD",
    )
    total_acb_cad: Decimal = Field(
        description="Total ACB deducted in CAD",
    )
    net_gain_loss_cad: Decimal = Field(
        description="Net capital gain / loss in CAD",
    )
    taxable_income_cad: Decimal = Field(
        description="Taxable portion (50% inclusion rate for capital gains)",
    )
