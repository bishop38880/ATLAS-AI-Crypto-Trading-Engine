"""
Paper-trade router — ATLAS side.

GET  /api/paper-trade/symbols             - read demo-symbol list from Redis cache
GET  /api/paper-trade/parallel-validation - synthetic USD replay vs ``signals`` history
POST /api/paper-trade                     - publish PaperTradeInstruction to Pub/Sub
                                            and (briefly) await the executor's ack

ATLAS does NOT execute trades. It is the wall-side proxy that lets the
frontend talk to PROMETHEUS without learning anything about Bitget.
"""
import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Optional

import asyncpg
import msgspec
import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.api._channel_reads import read_prices
from atlas.api.paper_parallel_simulation import SimHistoryRow, simulate_parallel_portfolio_usd
from atlas.api.schemas import (
    PaperDrawdownPoint,
    PaperEquityCurvePoint,
    PaperParallelValidationPayload,
    PaperTierWinBlock,
)
from atlas.dependencies import get_redis
from atlas.models.paper_trade import (
    DemoSymbol,
    PaperTradeAck,
    PaperTradeInstruction,
)
from atlas.settings import polaris_settings
from atlas.shared.config import PolarisSettings


router = APIRouter(prefix="/api/paper-trade", tags=["paper-trade"])


# ────────────────────────────────────────────────────────────────────
# Inline body / response models
# ────────────────────────────────────────────────────────────────────


class PaperTradeRequestBody(BaseModel):
    """The HTTP body the frontend posts. Wire-format only — translated
    into a PaperTradeInstruction before publishing."""
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(..., description="Base symbol e.g. BTCUSDT")
    side: Literal["LONG", "SHORT", "CLOSE"]
    size_usd_notional: Decimal = Field(..., gt=Decimal("0"))
    leverage: int = Field(default=5, ge=1, le=125)


class PaperTradeResponseBody(BaseModel):
    """HTTP response. Includes the executor's ack status if received
    within `paper_trade_ack_wait_seconds`, otherwise status='pending'."""
    model_config = ConfigDict(frozen=True)

    order_id: str
    status: Literal["pending", "accepted", "rejected", "executed", "failed"]
    bitget_order_id: str | None = None
    reason: str | None = None
    requested_at: datetime
    acknowledged_at: datetime | None = None


class DemoSymbolsResponseBody(BaseModel):
    """Wraps the DemoSymbol list with cache metadata for the frontend."""
    model_config = ConfigDict(frozen=True)

    symbols: list[DemoSymbol]
    fetched_at: datetime
    cache_age_seconds: int | None = None  # None if cache is missing


PARALLEL_VALIDATION_DISCLAIMER = (
    "USD paths are heuristic: Postgres ``signals`` rows omit historical marks — each asset "
    "series is reconstructed from ladder score deltas between observations and anchored "
    "to Redis spot NOW. Tier win-rates isolate whether 180+ entries outperform 150–179; "
    "they are comparable under this simulator, but not brokerage execution truth."
)


def _empty_parallel_validation() -> PaperParallelValidationPayload:
    zero_tier = PaperTierWinBlock(wins=0, trades=0, win_rate=0.0)
    flat_tiers = {
        "under_150": zero_tier,
        "150_179": zero_tier,
        "180_plus": zero_tier,
    }
    return PaperParallelValidationPayload(
        initial_usd="100000",
        ending_usd="100000",
        equity_curve=[],
        drawdown_curve=[],
        weekly_sharpe_annualized=0.0,
        tier_win_rates=flat_tiers,
        row_count_used=0,
        pricing_model="none",
        disclaimer=PARALLEL_VALIDATION_DISCLAIMER,
    )

# ────────────────────────────────────────────────────────────────────
# Routes
# ────────────────────────────────────────────────────────────────────


@router.get("/symbols", response_model=DemoSymbolsResponseBody)
async def get_demo_symbols(
    redis: redis_asyncio.Redis = Depends(get_redis),
) -> DemoSymbolsResponseBody:
    """Return the demo-symbol list from the Redis cache populated by
    PROMETHEUS. If the cache is empty (PROMETHEUS not yet started, or
    Bitget unreachable on cold start), return an empty list with
    `cache_age_seconds=None` — the frontend renders an "unavailable"
    state in that case rather than guessing."""
    raw = await redis.get(polaris_settings.paper_trade_symbol_cache_key)
    now = datetime.now(timezone.utc)

    if raw is None:
        logger.warning("paper_trade_symbols_cache_miss")
        return DemoSymbolsResponseBody(
            symbols=[],
            fetched_at=now,
            cache_age_seconds=None,
        )

    try:
        data = msgspec.json.decode(raw)
        symbols = [DemoSymbol(**d) for d in data]
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as exc:
        logger.error("paper_trade_symbols_decode_failed | exc={}", exc)
        # Stale or corrupt cache — surface as empty rather than 500.
        return DemoSymbolsResponseBody(
            symbols=[],
            fetched_at=now,
            cache_age_seconds=None,
        )

    age = _approximate_cache_age(symbols, now)
    return DemoSymbolsResponseBody(
        symbols=symbols, fetched_at=now, cache_age_seconds=age,
    )


@router.get(
    "/parallel-validation",
    response_model=PaperParallelValidationPayload,
    response_model_by_alias=True,
)
async def get_parallel_paper_validation(
    request: Request,
    redis: redis_asyncio.Redis = Depends(get_redis),
    limit: int = Query(1500, ge=50, le=5000),
    asset: Optional[str] = None,
) -> PaperParallelValidationPayload:
    """Rebuild a deterministic synthetic ledger from recent ``signals`` rows."""

    pool_raw = getattr(request.app.state, "db_pool", None)
    if pool_raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="historical_store_unavailable",
        )
    pool: asyncpg.Pool = pool_raw

    filter_asset = None
    if isinstance(asset, str) and asset.strip() != "":
        filter_asset = asset.strip()

    if filter_asset:
        query = (
            "SELECT id, asset, timestamp, total_score, decision, passes_gate "
            "FROM signals WHERE asset = $1 ORDER BY timestamp DESC LIMIT $2"
        )
        fetch_args: tuple[object, ...] = (filter_asset, limit)
    else:
        query = (
            "SELECT id, asset, timestamp, total_score, decision, passes_gate "
            "FROM signals ORDER BY timestamp DESC LIMIT $1"
        )
        fetch_args = (limit,)

    async with pool.acquire() as conn:
        rows_reverse = await conn.fetch(query, *fetch_args)

    if not rows_reverse:
        fallback = _empty_parallel_validation()
        return fallback.model_copy(update={"pricing_model": "historical_signals_missing"})

    timeline: list[SimHistoryRow] = []
    for row in reversed(rows_reverse):
        ts_value = row["timestamp"]
        if hasattr(ts_value, "replace"):
            stamp = ts_value
        else:
            stamp = datetime.fromisoformat(str(ts_value))

        timeline.append(
            SimHistoryRow(
                row_id=int(row["id"]),
                asset=str(row["asset"]).strip(),
                ts=stamp,
                total_score=float(row["total_score"]),
                decision=str(row["decision"]),
                passes_gate=bool(row["passes_gate"]),
            ),
        )

    settings_inst = PolarisSettings()
    anchors: dict[str, Decimal] = {}
    try:
        price_payloads = await read_prices(redis, settings_inst)
        for pkt in price_payloads:
            symbol_key = pkt.symbol.strip()
            anchors[symbol_key] = Decimal(str(pkt.price))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("parallel_validation_price_miss | reason={}", str(exc))

    sim_bundle = simulate_parallel_portfolio_usd(timeline, anchors)

    tiers_model = {
        key: PaperTierWinBlock(**value) for key, value in sim_bundle["tier_win_rates"].items()
    }

    equity_model = [
        PaperEquityCurvePoint(**point) for point in sim_bundle["equity_curve"]
    ]
    draw_model = [
        PaperDrawdownPoint(**point) for point in sim_bundle["drawdown_curve"]
    ]

    return PaperParallelValidationPayload(
        initial_usd=str(sim_bundle["initial_usd"]),
        ending_usd=str(sim_bundle["ending_usd"]),
        equity_curve=equity_model,
        drawdown_curve=draw_model,
        weekly_sharpe_annualized=float(sim_bundle["weekly_sharpe_annualized"]),
        tier_win_rates=tiers_model,
        row_count_used=int(sim_bundle["row_count_used"]),
        pricing_model=str(sim_bundle["pricing_model"]),
        disclaimer=PARALLEL_VALIDATION_DISCLAIMER,
    )


@router.post("", response_model=PaperTradeResponseBody)
async def submit_paper_trade(
    body: PaperTradeRequestBody,
    redis: redis_asyncio.Redis = Depends(get_redis),
) -> PaperTradeResponseBody:
    """Validate the requested symbol against the cache and publish a
    PaperTradeInstruction to the executor. Briefly waits for an ack so
    the frontend gets immediate feedback."""
    requested_at = datetime.now(timezone.utc)
    order_id = uuid.uuid4().hex

    match = await _lookup_demo_symbol(redis, body.symbol)
    if match is None:
        return PaperTradeResponseBody(
            order_id=order_id, status="rejected", requested_at=requested_at,
            reason=f"{body.symbol}_not_in_demo_universe",
        )

    instruction = PaperTradeInstruction(
        order_id=order_id, base_symbol=body.symbol, demo_symbol=match.symbol,
        margin_coin=match.margin_coin, side=body.side,
        size_usd_notional=body.size_usd_notional, leverage=body.leverage,
        requested_at=requested_at, source="manual_paper_trade",
    )

    ack = await _publish_and_wait_for_ack(redis, instruction)

    if ack is None:
        return PaperTradeResponseBody(
            order_id=order_id, status="pending", requested_at=requested_at,
        )

    return PaperTradeResponseBody(
        order_id=order_id, status=ack.status, bitget_order_id=ack.bitget_order_id,
        reason=ack.reason, requested_at=requested_at, acknowledged_at=ack.acknowledged_at,
    )


async def _lookup_demo_symbol(
    redis: redis_asyncio.Redis, base_symbol: str,
) -> DemoSymbol | None:
    """Read cache and return the DemoSymbol for base_symbol, or None."""
    raw = await redis.get(polaris_settings.paper_trade_symbol_cache_key)
    if raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="demo_symbol_cache_unavailable",
        )

    try:
        data = msgspec.json.decode(raw)
        symbols = [DemoSymbol(**d) for d in data]
    except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as exc:
        logger.error("paper_trade_lookup_decode_failed | exc={}", exc)
        raise HTTPException(503, detail="demo_symbol_cache_corrupt")

    return next((s for s in symbols if s.base_symbol == base_symbol), None)


async def _publish_and_wait_for_ack(
    redis: redis_asyncio.Redis, instruction: PaperTradeInstruction,
) -> PaperTradeAck | None:
    """Publish instruction and await ack on the order-specific channel."""
    order_id = instruction.order_id
    ack_channel = f"{polaris_settings.paper_trade_redis_channel}:ack:{order_id}"
    pubsub = redis.pubsub()
    await pubsub.subscribe(ack_channel)
    try:
        publish_channel = (
            f"{polaris_settings.paper_trade_redis_channel}:{instruction.base_symbol}"
        )
        await redis.publish(publish_channel, msgspec.json.encode(instruction.model_dump()))
        logger.info(
            "paper_trade_published | order_id={} | base={}",
            order_id, instruction.base_symbol,
        )
        return await _await_ack(pubsub, polaris_settings.paper_trade_ack_wait_seconds)
    finally:
        await pubsub.unsubscribe(ack_channel)
        await pubsub.aclose()


async def _await_ack(pubsub, timeout_s: float) -> PaperTradeAck | None:
    """Block up to `timeout_s` for the first ack message on the
    subscribed channel. Returns None on timeout."""
    deadline_task = asyncio.create_task(_listen_first(pubsub))
    try:
        return await asyncio.wait_for(deadline_task, timeout=timeout_s)
    except asyncio.TimeoutError:
        deadline_task.cancel()
        return None


async def _listen_first(pubsub) -> PaperTradeAck:
    async for message in pubsub.listen():
        if message.get("type") != "message":
            continue
        try:
            data = msgspec.json.decode(message["data"])
            return PaperTradeAck(**data)
        except (msgspec.DecodeError, msgspec.ValidationError, TypeError, ValueError) as exc:
            logger.warning("paper_trade_ack_invalid | exc={}", exc)
            continue
    # listen() never returns; keeps async-for happy
    raise RuntimeError("pubsub listen exhausted")  # pragma: no cover


def _approximate_cache_age(
    symbols: list[DemoSymbol], now: datetime,
) -> int | None:
    """Return age of the oldest fetched_at as integer seconds. Used
    purely for monitoring — the frontend renders this so operators
    can tell when the cache is stale."""
    if not symbols:
        return None
    oldest = min(s.fetched_at for s in symbols)
    return int((now - oldest).total_seconds())
