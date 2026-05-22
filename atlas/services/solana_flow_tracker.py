"""Solana flow tracker — rolling Redis windows for SOL/JUP exchange netflow.

Redis keys:
  solana:flow:{symbol}:events   — sorted set (score=timestamp)
  solana:flow:seen:{signature}  — dedup marker (2h TTL)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.providers.helius.models import FlowEvent, FlowSignals

_redis: redis_async.Redis | None = None  # type: ignore[type-arg]

_WINDOW_25H_SECONDS: int = 25 * 3600


class _StoredFlowEvent(msgspec.Struct):
    ts: float
    dir: str
    usd: float
    native: float
    src: str
    exch: str | None
    from_addr: str
    to_addr: str
    sig: str


def init_redis(redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
    """Bind Redis client from application startup."""
    global _redis
    _redis = redis_client


def _events_key(symbol: str) -> str:
    return f"solana:flow:{symbol.upper()}:events"


def _dedup_key(signature: str) -> str:
    return f"solana:flow:seen:{signature}"


def _empty_signals(symbol: str) -> FlowSignals:
    now_iso = datetime.now(timezone.utc).isoformat()
    return FlowSignals(
        symbol=symbol.upper(),
        exchange_netflow_1h=0.0,
        exchange_netflow_4h=0.0,
        exchange_netflow_24h=0.0,
        whale_tx_count_1h=0,
        whale_tx_count_4h=0,
        whale_tx_count_24h=0,
        flow_direction="neutral",
        largest_single_tx_24h=0.0,
        last_updated=now_iso,
    )


async def record_flow_event(event: FlowEvent) -> bool:
    """Record a classified flow event. Returns False if duplicate signature."""
    if _redis is None:
        logger.warning("solana_flow_redis_uninitialized")
        return False

    dedup = _dedup_key(event.signature)
    if await _redis.exists(dedup):
        return False

    await _redis.setex(dedup, 7200, b"1")
    stored = _StoredFlowEvent(
        ts=event.timestamp,
        dir=event.direction,
        usd=event.amount_usd,
        native=event.amount_native,
        src=event.source,
        exch=event.exchange,
        from_addr=event.from_address,
        to_addr=event.to_address,
        sig=event.signature,
    )
    payload = msgspec.json.encode(stored)
    key = _events_key(event.symbol)
    await _redis.zadd(key, {payload: event.timestamp})

    cutoff = time.time() - _WINDOW_25H_SECONDS
    await _redis.zremrangebyscore(key, "-inf", cutoff)

    logger.info(
        "solana_flow_recorded | symbol={} | direction={} | usd={} | source={} | exchange={}",
        event.symbol,
        event.direction,
        round(event.amount_usd, 2),
        event.source,
        event.exchange or "n/a",
    )
    return True


def _netflow_in_window(
    events: list[_StoredFlowEvent],
    now: float,
    window_seconds: int,
) -> tuple[float, int]:
    cutoff = now - window_seconds
    total = 0.0
    count = 0
    for entry in events:
        if entry.ts >= cutoff:
            if entry.dir == "inflow":
                total += entry.usd
            elif entry.dir == "outflow":
                total -= entry.usd
            count += 1
    return total, count


def _classify_direction(netflow_24h: float) -> Literal["inflow", "outflow", "neutral"]:
    if netflow_24h < -1_000_000:
        return "outflow"
    if netflow_24h > 1_000_000:
        return "inflow"
    return "neutral"


async def get_signals(symbol: str = "SOL") -> FlowSignals | None:
    """Compute scoring signals from rolling windows."""
    if _redis is None:
        return None

    symbol_upper = symbol.upper()
    key = _events_key(symbol_upper)
    now = time.time()
    raw_events = await _redis.zrangebyscore(key, now - (24 * 3600), now)

    if not raw_events:
        return _empty_signals(symbol_upper)

    events: list[_StoredFlowEvent] = []
    for raw in raw_events:
        try:
            events.append(msgspec.json.decode(raw, type=_StoredFlowEvent))
        except Exception:
            continue

    if not events:
        return _empty_signals(symbol_upper)

    nf_1h, _ = _netflow_in_window(events, now, 3600)
    nf_4h, _ = _netflow_in_window(events, now, 4 * 3600)
    nf_24h, count_24h = _netflow_in_window(events, now, 24 * 3600)
    _, count_1h = _netflow_in_window(events, now, 3600)
    _, count_4h = _netflow_in_window(events, now, 4 * 3600)

    largest = max((abs(entry.usd) for entry in events), default=0.0)
    direction = _classify_direction(nf_24h)

    return FlowSignals(
        symbol=symbol_upper,
        exchange_netflow_1h=round(nf_1h, 2),
        exchange_netflow_4h=round(nf_4h, 2),
        exchange_netflow_24h=round(nf_24h, 2),
        whale_tx_count_1h=count_1h,
        whale_tx_count_4h=count_4h,
        whale_tx_count_24h=count_24h,
        flow_direction=direction,
        largest_single_tx_24h=round(largest, 2),
        last_updated=datetime.now(timezone.utc).isoformat(),
    )
