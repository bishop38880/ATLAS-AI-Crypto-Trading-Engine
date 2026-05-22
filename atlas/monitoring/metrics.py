"""Redis counters for hourly monitoring observability."""

from __future__ import annotations

import msgspec
import redis.asyncio as redis_async
from loguru import logger

_METRICS_KEY = "polaris:monitoring:hourly:metrics"


async def record_fetch_outcome(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    *,
    provider: str,
    key_id: str,
    success: bool,
    latency_ms: float,
    throttled: bool = False,
    failover: bool = False,
) -> None:
    """Increment rolling counters consumed by the monitoring API."""
    try:
        raw = await redis_client.get(_METRICS_KEY)
        payload: dict[str, object] = {}
        if raw:
            decoded = msgspec.json.decode(raw if isinstance(raw, (bytes, bytearray)) else raw.encode())
            if isinstance(decoded, dict):
                payload = decoded
        totals = payload.get("totals", {})
        if not isinstance(totals, dict):
            totals = {}
        totals_key = f"{provider}:{key_id}"
        row = totals.get(totals_key, {})
        if not isinstance(row, dict):
            row = {}
        row["requests"] = int(row.get("requests", 0)) + 1
        if success:
            row["success"] = int(row.get("success", 0)) + 1
        if throttled:
            row["throttled"] = int(row.get("throttled", 0)) + 1
        if failover:
            row["failover"] = int(row.get("failover", 0)) + 1
        row["last_latency_ms"] = latency_ms
        totals[totals_key] = row
        payload["totals"] = totals
        await redis_client.setex(_METRICS_KEY, 86_400, msgspec.json.encode(payload))
    except Exception as exc:
        logger.warning("monitoring_metrics_write_failed | err={}", exc)


async def read_monitoring_metrics(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
) -> dict[str, object]:
    """Return metrics blob for API consumers."""
    raw = await redis_client.get(_METRICS_KEY)
    if not raw:
        return {"totals": {}}
    try:
        decoded = msgspec.json.decode(raw if isinstance(raw, (bytes, bytearray)) else raw.encode())
        if isinstance(decoded, dict):
            return decoded
    except Exception as exc:
        logger.warning("monitoring_metrics_read_failed | err={}", exc)
    return {"totals": {}}
