"""Tiered stop-loss data models — frozen Pydantic v2.

All financial fields use ``Decimal``.  ``frozen=True`` on both models
ensures immutability after construction.  Mutation goes through
``model_copy(update={...})``.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel


class StopTier(BaseModel, frozen=True):
    """Single tier of the 33/33/34 stop ladder."""

    tier: int                                    # 1, 2, or 3
    price: Decimal                               # trigger price
    size_pct: Decimal                            # fraction of position (0.33 / 0.33 / 0.34)
    size_usd: Decimal                            # USD notional for this tier
    size_base_coin: Decimal | None = None        # set after conversion at placement time
    bitget_order_id: str | None = None
    status: Literal[
        "pending", "placed", "paper_placed",
        "triggered", "cancelled", "failed",
    ] = "pending"


class TieredStopLoss(BaseModel, frozen=True):
    """Complete three-tier stop-loss ladder for a single trade."""

    trade_id: str
    asset: str
    position_side: Literal["long", "short"]
    entry_price: Decimal
    position_notional_usd: Decimal
    leverage: Decimal                            # audit only — not used in stop math

    tier_1: StopTier                             # 33 % @ -1× ATR (or -5 % fixed)
    tier_2: StopTier                             # 33 % @ -2× ATR (or -10 %)
    tier_3: StopTier                             # 34 % @ -3× ATR (or -15 %)

    atr_value: Decimal | None = None
    used_atr_pricing: bool = False
    placed_at: datetime | None = None
    all_placed: bool = False
