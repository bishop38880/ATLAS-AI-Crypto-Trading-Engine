"""Redis writers for the monitoring dashboard contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

_ALERT_BUFFER_LENGTH = 50
_LATENCY_HISTORY_LENGTH = 50
_STARTUP_SEQUENCE_TTL_S = 86_400
_AGENT_ZERO_TTL_S = 172_800
_COST_CAP_DEFAULT = "5.00"


def build_startup_sequence_payload(report: Any) -> dict[str, Any]:
    """Map a StartupReport-like object into the monitoring API shape."""
    steps: list[dict[str, Any]] = []
    for step in getattr(report, "steps", []):
        success = bool(getattr(step, "success", False))
        critical = bool(getattr(step, "critical", False))
        status = "PASSED" if success else "FAILED" if critical else "DEGRADED"
        steps.append(
            {
                "stepNumber": int(getattr(step, "step", 0)),
                "label": str(getattr(step, "name", "Unknown")),
                "durationSeconds": float(getattr(step, "duration_ms", 0.0)) / 1000.0,
                "status": status,
                "detail": str(getattr(step, "detail", "")),
            }
        )

    has_failures = any(step["status"] == "FAILED" for step in steps)
    has_degraded = any(step["status"] == "DEGRADED" for step in steps)
    overall_status = (
        "HAS_FAILURES" if has_failures else "HAS_DEGRADED" if has_degraded else "ALL_PASSED"
    )
    completed_at = getattr(report, "completed_at", None) or datetime.now(UTC)
    total_seconds = round(sum(float(step["durationSeconds"]) for step in steps), 3)
    return {
        "lastRunIso": _isoformat_utc(completed_at),
        "totalSeconds": total_seconds,
        "overallStatus": overall_status,
        "steps": steps,
    }


async def publish_startup_sequence(
    redis_client: redis_async.Redis,
    report: Any,
) -> None:
    """Persist the latest startup sequence for `/api/monitoring/startup`."""
    payload = build_startup_sequence_payload(report)
    await _set_json(redis_client, "polaris:startup:sequence", payload, ex=_STARTUP_SEQUENCE_TTL_S)


async def record_pipeline_cycle(
    redis_client: redis_async.Redis,
    *,
    cycle_id: str,
    asset: str,
    latency_ms: float,
    status: str,
    score: float | None = None,
    target_ms: int = 10_000,
) -> None:
    """Record end-to-end cycle metrics consumed by `/api/monitoring/latency`."""
    timestamp = datetime.now(UTC).isoformat()
    entry = {
        "timestamp": timestamp,
        "cycleId": cycle_id,
        "asset": asset,
        "latencyMs": max(0.0, float(latency_ms)),
        "status": status,
        "score": score,
    }
    await _push_json(redis_client, "polaris:latency:history", entry, _LATENCY_HISTORY_LENGTH)

    if status == "complete":
        await redis_client.incr("polaris:cycle_count")
        await redis_client.incr("polaris:signals_emitted")

    current_payload = await build_latency_current_payload(redis_client, target_ms=target_ms)
    await _set_json(redis_client, "polaris:latency:current", current_payload)


async def build_latency_current_payload(
    redis_client: redis_async.Redis,
    *,
    target_ms: int,
) -> dict[str, Any]:
    """Aggregate recent cycle latency history into percentile gauges."""
    raw_history = await redis_client.lrange("polaris:latency:history", 0, _LATENCY_HISTORY_LENGTH - 1)
    history: list[dict[str, Any]] = []
    latencies: list[float] = []
    breaches = 0
    for raw_entry in raw_history:
        entry = _decode_json(raw_entry)
        if not isinstance(entry, dict):
            continue
        history.append(entry)
        latency = entry.get("latencyMs")
        if isinstance(latency, (int, float)):
            latency_value = float(latency)
            latencies.append(latency_value)
            if latency_value > target_ms:
                breaches += 1

    breach_percent = (breaches / len(latencies) * 100.0) if latencies else 0.0
    return {
        "targetMs": target_ms,
        "breachCount24h": breaches,
        "breachPercent24h": breach_percent,
        "current": {
            "p50Ms": _percentile(latencies, 0.50),
            "p95Ms": _percentile(latencies, 0.95),
            "p99Ms": _percentile(latencies, 0.99),
            "maxMs": max(latencies) if latencies else 0,
        },
        "history": history,
    }


async def record_monitoring_alert(
    redis_client: redis_async.Redis,
    *,
    level: str,
    message: str,
    source: str,
    alert_id: str,
    dedupe_seconds: int = 300,
) -> None:
    """Append a debounced alert to `polaris:alerts`."""
    dedupe_key = f"polaris:alerts:dedupe:{source}:{alert_id}"
    inserted = await redis_client.set(dedupe_key, "1", ex=dedupe_seconds, nx=True)
    if not inserted:
        return

    payload = {
        "id": alert_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "level": level,
        "message": message,
        "source": source,
    }
    await _push_json(redis_client, "polaris:alerts", payload, _ALERT_BUFFER_LENGTH)


async def publish_agent_zero_schedule(
    redis_client: redis_async.Redis,
    *,
    target_hour_utc: int,
    next_run_iso: str,
    next_run_relative: str,
    last_run: dict[str, Any] | None,
) -> None:
    """Persist Agent Zero lifecycle data for `/api/monitoring/agent-zero`."""
    payload = {
        "scheduleCron": f"0 {target_hour_utc} * * *",
        "scheduleDescription": f"Daily at {target_hour_utc:02d}:00 UTC",
        "nextRunIso": next_run_iso,
        "nextRunRelative": next_run_relative,
        "lastRun": last_run,
    }
    await _set_json(redis_client, "polaris:agent_zero:schedule", payload, ex=_AGENT_ZERO_TTL_S)


async def record_llm_cost(
    redis_client: redis_async.Redis,
    *,
    provider: str,
    cost_usd: float,
    cost_cap_usd: float | None = None,
) -> None:
    """Increment daily LLM cost counters for `/api/monitoring/metrics`."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    day_key = "polaris:api_cost_day"
    raw_day = await redis_client.get(day_key)
    current_day = raw_day.decode("utf-8") if isinstance(raw_day, bytes) else raw_day
    if current_day != today:
        await redis_client.set(day_key, today, ex=_seconds_until_tomorrow())
        await redis_client.set("polaris:api_cost_today", "0.00", ex=_seconds_until_tomorrow())
        await _set_json(redis_client, "polaris:api_cost_breakdown", {}, ex=_seconds_until_tomorrow())

    await redis_client.incrbyfloat("polaris:api_cost_today", max(0.0, cost_usd))
    if cost_cap_usd is not None:
        await redis_client.set("polaris:api_cost_cap", f"{cost_cap_usd:.2f}")
    else:
        await redis_client.setnx("polaris:api_cost_cap", _COST_CAP_DEFAULT)

    breakdown = await _get_json(redis_client, "polaris:api_cost_breakdown")
    if not isinstance(breakdown, dict):
        breakdown = {}
    provider_entry = breakdown.get(provider)
    if not isinstance(provider_entry, dict):
        provider_entry = {"provider": provider, "costUsd": "0.00", "calls": 0}
    previous_cost = _safe_float(provider_entry.get("costUsd"))
    provider_entry["costUsd"] = f"{previous_cost + max(0.0, cost_usd):.6f}"
    provider_entry["calls"] = int(provider_entry.get("calls", 0)) + 1
    breakdown[provider] = provider_entry
    await _set_json(redis_client, "polaris:api_cost_breakdown", breakdown, ex=_seconds_until_tomorrow())


async def safe_monitoring_write(coro: Any, *, action: str) -> None:
    """Run a monitoring write without letting observability break hot paths."""
    try:
        await asyncio.wait_for(coro, timeout=2.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("monitoring_write_failed | action={} | error={}", action, str(exc))


async def _set_json(
    redis_client: redis_async.Redis,
    key: str,
    payload: Any,
    *,
    ex: int | None = None,
) -> None:
    encoded = msgspec.json.encode(payload)
    await redis_client.set(key, encoded, ex=ex)


async def _push_json(
    redis_client: redis_async.Redis,
    key: str,
    payload: Any,
    limit: int,
) -> None:
    encoded = msgspec.json.encode(payload)
    pipe = redis_client.pipeline()
    pipe.lpush(key, encoded)
    pipe.ltrim(key, 0, limit - 1)
    await pipe.execute()


async def _get_json(redis_client: redis_async.Redis, key: str) -> Any:
    raw = await redis_client.get(key)
    return _decode_json(raw)


def _decode_json(raw: Any) -> Any:
    if raw is None:
        return None
    try:
        return msgspec.json.decode(raw)
    except msgspec.DecodeError:
        return None


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0
    sorted_values = sorted(values)
    index = int(percentile * (len(sorted_values) - 1))
    return sorted_values[index]


def _seconds_until_tomorrow() -> int:
    now = datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(60, int((tomorrow - now).total_seconds()))


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _isoformat_utc(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    return datetime.now(UTC).isoformat()
