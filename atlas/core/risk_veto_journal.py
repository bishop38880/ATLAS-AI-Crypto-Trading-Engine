"""Append-only Risk Manager veto log in Redis (calibration / dashboard).

Events are stored newest-first in a capped list under ``risk:veto:events``.
Each entry is msgspec-encoded JSON with ISO timestamp, asset, cycle id, and reasons.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger

RISK_VETO_EVENTS_KEY = "risk:veto:events"
_MAX_EVENTS = 8_000


class RiskVetoEvent(msgspec.Struct):
    """One veto row persisted for the Risk Governor dashboard."""

    ts_iso: str
    asset: str
    cycle_id: str
    reasons: tuple[str, ...]


async def append_risk_veto_event(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    *,
    asset: str,
    reasons: list[str],
    cycle_id: str,
) -> None:
    """Record a Risk agent veto for historical calibration charts."""
    event = RiskVetoEvent(
        ts_iso=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        asset=asset.strip() or "UNKNOWN",
        cycle_id=cycle_id.strip() or "—",
        reasons=tuple(reasons) if reasons else ("unspecified",),
    )
    try:
        payload = msgspec.json.encode(event)
        pipe = redis_client.pipeline()
        pipe.lpush(RISK_VETO_EVENTS_KEY, payload)
        pipe.ltrim(RISK_VETO_EVENTS_KEY, 0, _MAX_EVENTS - 1)
        await pipe.execute()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("risk_veto_journal_append_failed | asset={} | err={}", asset, str(exc))


async def load_risk_veto_events_raw(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    *,
    max_items: int = 12_000,
) -> list[dict[str, Any]]:
    """Decode veto events from Redis (newest first)."""
    raw_items = await redis_client.lrange(RISK_VETO_EVENTS_KEY, 0, max(0, max_items - 1))  # type: ignore[misc]
    out: list[dict[str, Any]] = []
    for raw in raw_items:
        try:
            decoded = msgspec.json.decode(raw, type=RiskVetoEvent)
            out.append(
                {
                    "ts_iso": decoded.ts_iso,
                    "asset": decoded.asset,
                    "cycle_id": decoded.cycle_id,
                    "reasons": list(decoded.reasons),
                }
            )
        except Exception:
            continue
    return out
