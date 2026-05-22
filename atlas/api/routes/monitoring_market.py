"""Hourly market monitoring API — latest cycle + fetch metrics."""

from __future__ import annotations

from typing import Any

import msgspec
from fastapi import APIRouter, Request

from atlas.monitoring.metrics import read_monitoring_metrics

router = APIRouter(prefix="/api/monitoring", tags=["monitoring"])


@router.get("/hourly-market")
async def get_hourly_market_snapshot(request: Request) -> dict[str, Any]:
    """Return the latest hourly monitor cycle from Redis."""
    redis = request.app.state.redis
    raw = await redis.get("polaris:monitoring:hourly:latest")
    if not raw:
        return {"status": "empty", "cycle": None}
    try:
        payload = msgspec.json.decode(raw if isinstance(raw, (bytes, bytearray)) else raw.encode())
        if isinstance(payload, dict):
            return {"status": "ok", "cycle": payload}
    except Exception:
        pass
    return {"status": "corrupt", "cycle": None}


@router.get("/hourly-market/metrics")
async def get_hourly_market_metrics(request: Request) -> dict[str, Any]:
    """Provider/key latency and success counters."""
    redis = request.app.state.redis
    metrics = await read_monitoring_metrics(redis)
    return {"status": "ok", "metrics": metrics}
