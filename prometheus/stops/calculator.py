"""ATR-aware stop ladder calculator.

Primary: ATR-based stops (1×, 2×, 3× ATR_14 from canonical Redis key).
Fallback: fixed percentages (−5 %, −10 %, −15 %).
Per tier, the TIGHTER of the two is selected so we never exceed the
fixed ceiling.

Allocation: 33 % / 33 % / 34 % — tier 3 absorbs rounding.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Literal

import msgspec
import redis.asyncio as redis_asyncio

from prometheus.stops.models import StopTier, TieredStopLoss


class StopLadderCalculator:
    """Compute a three-tier stop-loss ladder from ATR or fixed offsets."""

    FIXED_STOP_PCTS: list[Decimal] = [
        Decimal("0.05"), Decimal("0.10"), Decimal("0.15"),
    ]
    ATR_MULTIPLES: list[Decimal] = [
        Decimal("1.0"), Decimal("2.0"), Decimal("3.0"),
    ]
    TIER_SIZES: list[Decimal] = [
        Decimal("0.33"), Decimal("0.33"), Decimal("0.34"),
    ]
    ATR_KEY_FMT: str = "atlas:atr:{asset}:{timeframe}"

    def __init__(self, redis_client: redis_asyncio.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    async def calculate(
        self,
        trade_id: str,
        asset: str,
        side: Literal["long", "short"],
        entry_price: Decimal,
        position_notional_usd: Decimal,
        leverage: Decimal,
        timeframe: str = "1h",
    ) -> TieredStopLoss:
        """Build the full 3-tier ladder."""
        atr = await self._fetch_atr(asset, timeframe)
        tiers = self._build_tiers(
            entry_price, side, position_notional_usd, atr,
        )
        return TieredStopLoss(
            trade_id=trade_id,
            asset=asset,
            position_side=side,
            entry_price=entry_price,
            position_notional_usd=position_notional_usd,
            leverage=leverage,
            tier_1=tiers[0],
            tier_2=tiers[1],
            tier_3=tiers[2],
            atr_value=atr,
            used_atr_pricing=atr is not None,
        )

    # ── internals ─────────────────────────────────────────────────────

    async def _fetch_atr(
        self, asset: str, timeframe: str,
    ) -> Decimal | None:
        """Read ATR from the canonical Redis key."""
        key = self.ATR_KEY_FMT.format(asset=asset, timeframe=timeframe)
        try:
            raw = await asyncio.wait_for(
                self._redis.get(key), timeout=5.0,
            )
        except (asyncio.TimeoutError, Exception):
            return None
        if raw is None:
            return None
        data = msgspec.json.decode(raw)
        atr_val = data.get("atr") if isinstance(data, dict) else None
        if atr_val is None:
            return None
        return Decimal(str(atr_val))

    def _build_tiers(
        self,
        entry_price: Decimal,
        side: Literal["long", "short"],
        notional_usd: Decimal,
        atr: Decimal | None,
    ) -> list[StopTier]:
        """Compute trigger prices and per-tier USD sizes."""
        tiers: list[StopTier] = []
        for i in range(3):
            price = self._tier_price(
                entry_price, side, atr, i,
            )
            size_pct = self.TIER_SIZES[i]
            size_usd = notional_usd * size_pct
            tiers.append(StopTier(
                tier=i + 1,
                price=price,
                size_pct=size_pct,
                size_usd=size_usd,
            ))
        return tiers

    def _tier_price(
        self,
        entry: Decimal,
        side: Literal["long", "short"],
        atr: Decimal | None,
        idx: int,
    ) -> Decimal:
        """Pick the tighter of ATR-based and fixed-pct stop prices."""
        fixed_offset = entry * self.FIXED_STOP_PCTS[idx]
        if atr is not None:
            atr_offset = atr * self.ATR_MULTIPLES[idx]
            offset = min(atr_offset, fixed_offset)
        else:
            offset = fixed_offset
        if side == "long":
            return entry - offset
        return entry + offset
