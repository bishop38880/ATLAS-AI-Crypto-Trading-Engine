"""Monitoring API routes — hydrated from Redis and Postgres.

Endpoints:
    GET /api/monitoring/startup   → StartupSequenceData
    GET /api/monitoring/latency   → PipelineLatencyData
    GET /api/monitoring/agent-zero → AgentZeroScheduleData
    GET /api/monitoring/alerts    → AlertEntry[]
    GET /api/monitoring/metrics   → KeyMetricsData
    GET /api/test-floor           → TestFloorData
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from fastapi import APIRouter, Request
from redis.asyncio import Redis
from loguru import logger
import msgspec

from atlas.api.monitoring_startup_checks import startup_agents_step_ok, startup_prices_step_ok

router = APIRouter(prefix="/api", tags=["monitoring"])


# ────────────────────────────────────────────────────────────────
# GET /api/monitoring/startup
# ────────────────────────────────────────────────────────────────

@router.get("/monitoring/startup")
async def get_startup(request: Request) -> Dict[str, Any]:
    """Return startup sequence from Redis cache or sensible defaults."""
    redis: Redis = request.app.state.redis

    raw = await redis.get("polaris:startup:sequence")
    if raw:
        try:
            return msgspec.json.decode(raw)
        except Exception:
            pass

    steps: List[Dict[str, Any]] = []

    try:
        redis_ok = bool(await redis.ping())
    except Exception as exc:
        logger.warning("monitoring_startup_redis_ping_failed | err={}", str(exc))
        redis_ok = False
    steps.append({
        "stepNumber": 1,
        "label": "Redis Connectivity",
        "durationSeconds": 0.1,
        "status": "PASSED" if redis_ok else "DEGRADED",
    })

    pool = getattr(request.app.state, "db_pool", None)
    steps.append({
        "stepNumber": 2,
        "label": "Postgres Connectivity",
        "durationSeconds": 0.1,
        "status": "PASSED" if pool is not None else "DEGRADED",
    })

    agents_ok = await startup_agents_step_ok(redis)
    steps.append({
        "stepNumber": 3,
        "label": "Agents Connectivity",
        "durationSeconds": 0.1,
        "status": "PASSED" if agents_ok else "DEGRADED",
    })

    prices_ok = await startup_prices_step_ok(redis)
    steps.append({
        "stepNumber": 4,
        "label": "Prices Connectivity",
        "durationSeconds": 0.1,
        "status": "PASSED" if prices_ok else "DEGRADED",
    })

    has_failures = any(s["status"] == "FAILED" for s in steps)
    has_degraded = any(s["status"] == "DEGRADED" for s in steps)
    overall = "HAS_FAILURES" if has_failures else "HAS_DEGRADED" if has_degraded else "ALL_PASSED"

    return {
        "lastRunIso": datetime.now(UTC).isoformat(),
        "totalSeconds": 0.5,
        "overallStatus": overall,
        "steps": steps,
    }


# ────────────────────────────────────────────────────────────────
# GET /api/monitoring/latency
# ────────────────────────────────────────────────────────────────

@router.get("/monitoring/latency")
async def get_latency(request: Request) -> Dict[str, Any]:
    """Return pipeline latency from Redis histograms or zero-defaults."""
    redis: Redis = request.app.state.redis

    raw = await redis.get("polaris:latency:current")
    if raw:
        try:
            return msgspec.json.decode(raw)
        except Exception:
            pass

    # Pull last N cycle latencies from a Redis list
    history: List[Dict[str, Any]] = []
    raw_history = await redis.lrange("polaris:latency:history", 0, 49)
    for item in raw_history:
        try:
            entry = msgspec.json.decode(item)
            history.append(entry)
        except Exception:
            continue

    return {
        "targetMs": 10000,
        "breachCount24h": 0,
        "breachPercent24h": 0.0,
        "current": {
            "p50Ms": 0,
            "p95Ms": 0,
            "p99Ms": 0,
            "maxMs": 0,
        },
        "history": history,
    }


# ────────────────────────────────────────────────────────────────
# GET /api/monitoring/agent-zero
# ────────────────────────────────────────────────────────────────

@router.get("/monitoring/agent-zero")
async def get_agent_zero(request: Request) -> Dict[str, Any]:
    """Return Agent Zero lifecycle data from Redis or defaults."""
    redis: Redis = request.app.state.redis

    raw = await redis.get("polaris:agent_zero:schedule")
    if raw:
        try:
            return msgspec.json.decode(raw)
        except Exception:
            pass

    return {
        "scheduleCron": "0 2 * * *",
        "scheduleDescription": "Daily at 02:00 UTC",
        "nextRunIso": "",
        "nextRunRelative": "unknown",
        "lastRun": None,
    }


# ────────────────────────────────────────────────────────────────
# GET /api/monitoring/alerts
# ────────────────────────────────────────────────────────────────

@router.get("/monitoring/alerts")
async def get_alerts(request: Request) -> List[Dict[str, Any]]:
    """Return recent alerts from Redis alert buffer."""
    redis: Redis = request.app.state.redis

    alerts: List[Dict[str, Any]] = []
    raw_alerts = await redis.lrange("polaris:alerts", 0, 49)
    for item in raw_alerts:
        try:
            entry = msgspec.json.decode(item)
            alerts.append(entry)
        except Exception:
            continue

    # If no alerts in Redis, check agent health for degraded agents
    if not alerts:
        async for key in redis.scan_iter(match="agent:*:status"):
            if isinstance(key, bytes):
                key = key.decode("utf-8")
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                data = msgspec.json.decode(raw)
                st = data.get("status", "GREEN")
                if st in ("RED", "FAILED"):
                    name = key.split(":")[1]
                    alerts.append({
                        "id": f"auto-{name}",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "level": "ERROR",
                        "message": f"Agent '{name}' is in {st} state",
                        "source": "agent-health",
                    })
                elif st in ("DEGRADED", "YELLOW"):
                    name = key.split(":")[1]
                    alerts.append({
                        "id": f"auto-{name}",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "level": "WARN",
                        "message": f"Agent '{name}' is degraded",
                        "source": "agent-health",
                    })
            except Exception:
                continue

    return alerts


# ────────────────────────────────────────────────────────────────
# GET /api/monitoring/metrics
# ────────────────────────────────────────────────────────────────

@router.get("/monitoring/metrics")
async def get_metrics(request: Request) -> Dict[str, Any]:
    """Return key operational metrics synthesized from Redis."""
    redis: Redis = request.app.state.redis

    # Cycle count
    cycle_raw = await redis.get("polaris:cycle_count")
    cycle_count = int(cycle_raw) if cycle_raw else 0

    # Signals emitted
    signals_raw = await redis.get("polaris:signals_emitted")
    signals_emitted = int(signals_raw) if signals_raw else 0

    # Agent count for uptime heuristic
    agent_count = 0
    healthy_count = 0
    async for key in redis.scan_iter(match="agent:*:status"):
        agent_count += 1
        raw = await redis.get(key)
        if raw:
            try:
                data = msgspec.json.decode(raw)
                if data.get("status") in ("GREEN", "READY"):
                    healthy_count += 1
            except Exception:
                pass

    uptime_pct = f"{(healthy_count / max(agent_count, 1)) * 100:.1f}"

    # API cost (best-effort from Redis)
    cost_raw = await redis.get("polaris:api_cost_today")
    api_cost = str(cost_raw.decode() if isinstance(cost_raw, bytes) else cost_raw) if cost_raw else "0.00"
    cost_cap_raw = await redis.get("polaris:api_cost_cap")
    api_cost_cap = str(cost_cap_raw.decode() if isinstance(cost_cap_raw, bytes) else cost_cap_raw) if cost_cap_raw else "5.00"

    try:
        cost_pct = f"{(float(api_cost) / max(float(api_cost_cap), 0.01)) * 100:.0f}"
    except Exception:
        cost_pct = "0"

    cost_breakdown: List[Dict[str, Any]] = []
    cost_breakdown_raw = await redis.get("polaris:api_cost_breakdown")
    if cost_breakdown_raw:
        try:
            decoded_breakdown = msgspec.json.decode(cost_breakdown_raw)
            if isinstance(decoded_breakdown, dict):
                cost_breakdown = [
                    item
                    for item in decoded_breakdown.values()
                    if isinstance(item, dict)
                ]
            elif isinstance(decoded_breakdown, list):
                cost_breakdown = [
                    item
                    for item in decoded_breakdown
                    if isinstance(item, dict)
                ]
        except Exception:
            logger.warning("monitoring_cost_breakdown_decode_failed")

    # Test floor
    test_floor_raw = await redis.get("polaris:test_floor:current")
    test_floor = int(test_floor_raw) if test_floor_raw else 0

    scores: List[float] = []
    raw_latency_history = await redis.lrange("polaris:latency:history", 0, 49)
    for raw_entry in raw_latency_history:
        try:
            entry = msgspec.json.decode(raw_entry)
            score = entry.get("score") if isinstance(entry, dict) else None
            if isinstance(score, (int, float)):
                scores.append(float(score))
        except Exception:
            continue
    avg_score = round(sum(scores) / len(scores), 2) if scores else 0

    return {
        "uptimePercent": uptime_pct,
        "cycleCount": cycle_count,
        "signalsEmitted": signals_emitted,
        "avgScore": avg_score,
        "testFloor": test_floor,
        "apiCostToday": api_cost,
        "apiCostCap": api_cost_cap,
        "costPercent": cost_pct,
        "burnRateVs7DayAvg": "0",
        "costBreakdown": cost_breakdown,
    }


# ────────────────────────────────────────────────────────────────
# GET /api/test-floor
# ────────────────────────────────────────────────────────────────

@router.get("/test-floor")
async def get_test_floor(request: Request) -> Dict[str, Any]:
    """Return test floor tracking data from Redis."""
    redis: Redis = request.app.state.redis

    raw = await redis.get("polaris:test_floor:data")
    if raw:
        try:
            return msgspec.json.decode(raw)
        except Exception:
            pass

    current_raw = await redis.get("polaris:test_floor:current")
    current = int(current_raw) if current_raw else 0
    ath_raw = await redis.get("polaris:test_floor:ath")
    ath = int(ath_raw) if ath_raw else current

    return {
        "currentFloor": current,
        "allTimeHigh": ath,
        "whenAchievedRelative": "unknown",
        "targetFloor": 100,
        "breached": current < ath,
        "history": [],
    }


@router.get("/test-floor/history")
async def get_test_floor_history(request: Request) -> Dict[str, Any]:
    """Return test-floor history in the path expected by monitoring clients."""
    payload = await get_test_floor(request)
    return {"history": payload.get("history", [])}
