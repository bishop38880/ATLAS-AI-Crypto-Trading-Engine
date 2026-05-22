"""Paper-trade executor.

Subscribes to `polaris:paper_trade:*` Pub/Sub. For each instruction:
1. Validate schema version + payload shape.
2. Validate symbol against the demo-symbol cache.
3. If dry-run is disabled and Bitget demo writes are enabled, place a
   standalone plan order on Bitget via BitgetExecutionClient.
4. Publish a PaperTradeAck on `polaris:paper_trade:ack:{order_id}`.

Dry-run and demo-write controls are separate so the subscriber can be
tested end-to-end without exchange writes.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

import httpx
import msgspec
import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.execution.bitget_client import (
    BitgetExecutionClient,
    PlacePlanOrderRequest,
)
from prometheus.services.demo_symbols import (
    base_to_demo,
    fetch_demo_symbols,
)
from prometheus.services.paper_trade_models import (
    PaperTradeAck,
    PaperTradeInstruction,
)
from prometheus.settings import PrometheusSettings, prometheus_settings


SUPPORTED_MAJOR_VERSION = 1
AckStatus = Literal["accepted", "rejected", "executed", "failed"]


class PaperTradeExecutor:
    """Long-running subscriber. Constructed once at app startup."""

    def __init__(
        self,
        redis: redis_asyncio.Redis,
        http_client: httpx.AsyncClient,
        bitget_client: BitgetExecutionClient,
        settings: PrometheusSettings,
    ) -> None:
        self._redis = redis
        self._http_client = http_client
        self._bitget = bitget_client
        self._settings = settings
        self._channel_pattern = (
            f"{settings.paper_trade_redis_channel}:*"
        )

    async def run(self) -> None:
        """Subscribe and dispatch. Cancellable via task.cancel()."""
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe(self._channel_pattern)
        logger.info(
            "paper_trade_executor_started | pattern={} | dry_run={} | demo_write={}",
            self._channel_pattern,
            _paper_trade_dry_run(self._settings),
            _bitget_demo_write_enabled(self._settings),
        )
        try:
            async for message in pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            logger.info("paper_trade_executor_cancelled")
            await pubsub.punsubscribe(self._channel_pattern)
            raise

    async def _handle_message(self, message: dict) -> None:
        try:
            # Decode as dict first
            data = msgspec.json.decode(message["data"], type=dict)
            instruction = PaperTradeInstruction(**data)
        except (msgspec.ValidationError, TypeError, ValueError) as exc:
            logger.warning("paper_trade_invalid_payload | exc={}", exc)
            return
        except Exception:
            logger.exception("paper_trade_decode_unexpected")
            return

        if not _supported_version(instruction.schema_version):
            await self._reject(instruction, reason="unsupported_schema_version")
            return

        await self._dispatch(instruction)

    async def _dispatch(self, instruction: PaperTradeInstruction) -> None:
        # Validate symbol against the cache.
        symbols = await fetch_demo_symbols(self._redis, self._http_client)
        match = base_to_demo(instruction.base_symbol, symbols)
        if match is None:
            await self._reject(
                instruction,
                reason=f"{instruction.base_symbol}_not_in_demo_universe",
            )
            return

        if _paper_trade_dry_run(self._settings):
            await self._ack(
                instruction,
                status="accepted",
                reason="paper_trade_dry_run",
            )
            return

        if not _bitget_demo_write_enabled(self._settings):
            await self._ack(
                instruction,
                status="accepted",
                reason="bitget_demo_write_disabled",
            )
            return

        await self._place(instruction, match)

    async def _place(
        self, instruction: PaperTradeInstruction, match,
    ) -> None:
        """Execute the order on Bitget."""
        request = await self._build_plan_request(instruction, match)
        result = await self._bitget.place_plan_order(request)
        await self._process_bitget_result(instruction, result)

    async def _build_plan_request(self, ins: PaperTradeInstruction, match) -> PlacePlanOrderRequest:
        """Construct the Bitget plan order request."""
        side = _side_for_bitget(ins.side)
        trade_side = "open" if ins.side in ("LONG", "SHORT") else "close"
        side_lit: Literal["buy", "sell"] = "buy" if side == "buy" else "sell"

        size_base = await self._bitget.usd_notional_to_base_coin_size(
            symbol=match.symbol,
            notional_usd=ins.size_usd_notional,
        )

        return PlacePlanOrderRequest(
            symbol=match.symbol,
            product_type=match.contract_type,
            margin_mode="isolated",
            margin_coin=match.margin_coin,
            size=size_base,
            side=side_lit,
            trade_side=trade_side,  # type: ignore[arg-type]
            order_type="market",
            plan_type="normal_plan",
            trigger_price=Decimal("0"),
            trigger_type="mark_price",
        )

    async def _process_bitget_result(self, ins: PaperTradeInstruction, result) -> None:
        """Acknowledge based on execution result."""
        if result.success:
            await self._ack(ins, status="executed", bitget_order_id=result.order_id)
        else:
            await self._ack(ins, status="failed", reason=str(result.errors)[:500])

    async def _reject(
        self, instruction: PaperTradeInstruction, reason: str,
    ) -> None:
        await self._ack(instruction, status="rejected", reason=reason)

    async def _ack(
        self,
        instruction: PaperTradeInstruction,
        status: AckStatus,
        bitget_order_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        ack = PaperTradeAck(
            order_id=instruction.order_id,
            status=status,
            bitget_order_id=bitget_order_id,
            reason=reason,
            acknowledged_at=datetime.now(timezone.utc),
        )
        channel = (
            f"{self._settings.paper_trade_redis_channel}:ack:"
            f"{instruction.order_id}"
        )
        await self._redis.publish(channel, msgspec.json.encode(ack.model_dump()))
        logger.info(
            "paper_trade_acked | order_id={} | status={} | reason={}",
            instruction.order_id, status, reason,
        )


def _supported_version(version: str) -> bool:
    try:
        major = int(version.split(".", 1)[0])
        return major == SUPPORTED_MAJOR_VERSION
    except (ValueError, IndexError):
        return False


def _side_for_bitget(side) -> str:
    """Map LONG/SHORT/CLOSE to Bitget's buy/sell semantics. CLOSE side
    is determined by the existing position's direction, so the executor
    leaves it for BitgetExecutionClient to resolve via close_position."""
    if side == "LONG":
        return "buy"
    if side == "SHORT":
        return "sell"
    return "close"  # caller will route differently for close-by-side


def _bool_setting(settings: Any, name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    return value if isinstance(value, bool) else default


def _paper_trade_dry_run(settings: Any) -> bool:
    explicit = getattr(settings, "paper_trade_dry_run", None)
    if isinstance(explicit, bool):
        return explicit

    legacy_enabled = getattr(settings, "paper_trade_enabled", None)
    if isinstance(legacy_enabled, bool):
        return not legacy_enabled

    return True


def _bitget_demo_write_enabled(settings: Any) -> bool:
    return _bool_setting(settings, "bitget_demo_write_enabled", False)
