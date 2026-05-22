"""Stop ladder placer — places three tiers as standalone Bitget plan orders.

All exchange interaction goes through ``BitgetExecutionClient``.
No raw HTTP, no manual signing, no position-attached TPSL endpoint.
Size is always base-coin via ``usd_notional_to_base_coin_size()``.
Paper-trading guard wraps every exchange call.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.execution.bitget_client import BitgetExecutionClient
from prometheus.execution.models import (
    PlaceOrderResult,
    PlacePlanOrderRequest,
)
from prometheus.stops.models import StopTier, TieredStopLoss


class StopLadderPlacer:
    """Places the three stop tiers as standalone plan orders on Bitget."""

    def __init__(
        self,
        redis_client: redis_asyncio.Redis,  # type: ignore[type-arg]
        pg_pool: asyncpg.Pool,
        bitget_client: BitgetExecutionClient,
        paper_trading: bool = True,
    ) -> None:
        self._redis = redis_client
        self._pg = pg_pool
        self._bitget = bitget_client
        self._paper = paper_trading

    async def place_all(
        self, ladder: TieredStopLoss,
    ) -> TieredStopLoss:
        """Place all three tiers. Returns updated ladder."""
        updated_tiers: list[StopTier] = []
        for tier in (ladder.tier_1, ladder.tier_2, ladder.tier_3):
            result = await self._place_tier(ladder, tier)
            updated_tiers.append(result)
            await self._persist_tier(ladder.trade_id, result)

        all_ok = all(
            t.status in ("placed", "paper_placed")
            for t in updated_tiers
        )
        return ladder.model_copy(update={
            "tier_1": updated_tiers[0],
            "tier_2": updated_tiers[1],
            "tier_3": updated_tiers[2],
            "all_placed": all_ok,
            "placed_at": datetime.now(tz=timezone.utc),
        })

    async def _place_tier(
        self, ladder: TieredStopLoss, tier: StopTier,
    ) -> StopTier:
        """Single-tier placement — paper-aware."""
        if self._paper:
            return self._paper_place(ladder, tier)

        symbol = f"{ladder.asset}USDT"
        close_side = _close_side(ladder.position_side)

        size_base = await self._convert_size(
            symbol, tier.size_usd, tier.price,
        )

        result = await self._submit_plan_order(
            symbol, close_side, size_base, tier.price,
        )

        if result.success and result.order_id:
            return tier.model_copy(update={
                "bitget_order_id": result.order_id,
                "size_base_coin": size_base,
                "status": "placed",
            })

        logger.error(
            "stop_placement_failed | tier={} | trade_id={} | "
            "errors={} | size_base={} | trigger={}",
            tier.tier, ladder.trade_id,
            result.errors, size_base, tier.price,
        )
        return tier.model_copy(update={"status": "failed"})

    async def _convert_size(
        self,
        symbol: str,
        notional_usd: Decimal,
        reference_price: Decimal,
    ) -> Decimal:
        """USD notional → base-coin size via the execution client."""
        return await self._bitget.usd_notional_to_base_coin_size(
            symbol=symbol,
            notional_usd=notional_usd,
            reference_price=reference_price,
        )

    async def _submit_plan_order(
        self,
        symbol: str,
        close_side: str,
        size_base: Decimal,
        trigger_price: Decimal,
    ) -> PlaceOrderResult:
        """Build and submit the plan order request."""

        req = PlacePlanOrderRequest(
            symbol=symbol,
            product_type="USDT-FUTURES",
            margin_mode="isolated",
            margin_coin="USDT",
            size=size_base,
            side=close_side,  # type: ignore[arg-type]
            trade_side="close",
            trigger_price=trigger_price,
            trigger_type="mark_price",
            order_type="market",
            plan_type="normal_plan",
        )
        return await self._bitget.place_plan_order(req)

    @staticmethod
    def _paper_place(
        ladder: TieredStopLoss, tier: StopTier,
    ) -> StopTier:
        """Return paper-placed stub without touching Bitget."""
        return tier.model_copy(update={
            "status": "paper_placed",
            "bitget_order_id": f"paper-{ladder.trade_id}-{tier.tier}",
        })

    async def cancel_tier(self, order_id: str) -> bool:
        """Cancel a placed plan order via the execution client."""
        return await self._bitget.cancel_plan_order(
            order_id=order_id,
            product_type="USDT-FUTURES",
        )

    async def _persist_tier(
        self, trade_id: str, tier: StopTier,
    ) -> None:
        """Write tier to stop_ladder_tiers. asyncpg takes Decimal."""
        await asyncio.wait_for(
            self._pg.execute(
                """
                INSERT INTO stop_ladder_tiers
                    (trade_id, tier, price, size_pct, size_usd,
                     size_base_coin, bitget_order_id, status)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (trade_id, tier) DO UPDATE SET
                    bitget_order_id = EXCLUDED.bitget_order_id,
                    size_base_coin  = EXCLUDED.size_base_coin,
                    status          = EXCLUDED.status
                """,
                trade_id, tier.tier,
                tier.price, tier.size_pct, tier.size_usd,
                tier.size_base_coin, tier.bitget_order_id, tier.status,
            ),
            timeout=5.0,
        )


def _close_side(position_side: str) -> str:
    """Map position side to the order side that closes it."""
    return "sell" if position_side == "long" else "buy"
