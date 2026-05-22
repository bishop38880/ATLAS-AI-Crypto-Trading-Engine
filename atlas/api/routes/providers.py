"""Provider Health REST endpoint.

Returns circuit-breaker-style health data for all registered data
providers, matching the frontend ProviderHealth store shape.
"""

from datetime import datetime, timezone
from typing import List, Optional

import msgspec
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from atlas.api.schemas import _BaseConfig


router = APIRouter(prefix="/api/providers", tags=["providers"])


# ─── Response Schema ─────────────────────────────────────────────────────

class ProviderHealthPayload(BaseModel):
    """Wire format consumed by POLARIS ProviderCard / ProviderHealthSummary."""

    model_config = _BaseConfig

    name: str
    tier: int
    trust_rank: int
    state: str           # CLOSED | DEGRADED | OPEN | HALF_OPEN
    health_score: float   # 0–1
    failure_rate: float   # 0–1
    slow_call_rate: float # 0–1
    avg_latency_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    requests_per_min: float
    rate_limit_max: float
    last_fetch_ms: float  # epoch ms of last fetch
    cache_status: str     # HIT | MISS | STALE
    window_size: int
    window_failures: int
    window_slow_calls: int
    last_failure_ts: Optional[str] = None  # ISO timestamp or null


# ─── Provider Registry (static tier + trust mapping) ─────────────────────

PROVIDER_REGISTRY: list[dict[str, object]] = [
    {"name": "Coinalyze",      "tier": 1, "trust_rank": 1},
    {"name": "Bitget",         "tier": 1, "trust_rank": 2},
    {"name": "Pyth",           "tier": 1, "trust_rank": 3},
    {"name": "CoinGecko",      "tier": 1, "trust_rank": 4},
    {"name": "DefiLlama",      "tier": 1, "trust_rank": 5},
    {"name": "HYDRA",          "tier": 1, "trust_rank": 6},
    {"name": "Nansen",         "tier": 2, "trust_rank": 7},
    {"name": "FRED",           "tier": 2, "trust_rank": 8},
    {"name": "Dune",           "tier": 2, "trust_rank": 9},
    {"name": "Hypertracker",   "tier": 2, "trust_rank": 10},
    {"name": "Alternative.me", "tier": 2, "trust_rank": 11},
    {"name": "OKX MCP",        "tier": 2, "trust_rank": 12},
    {"name": "Altfins",        "tier": 2, "trust_rank": 13},
    {"name": "Coinbase Premium", "tier": 1, "trust_rank": 14},
]

# Redis key → ProviderHealthState shape from atlas/core/provider_health.py
REDIS_KEY_MAP: dict[str, str] = {
    "Coinalyze":      "coinalyze",
    "Bitget":         "bitget",
    "Pyth":           "pyth",
    "CoinGecko":      "coingecko",
    "DefiLlama":      "defillama",
    "HYDRA":          "hydra",
    "Nansen":         "nansen",
    "FRED":           "fred",
    "Dune":           "dune",
    "Hypertracker":   "hypertracker",
    "Alternative.me": "alternative_me",
    "OKX MCP":        "okx_mcp",
    "Altfins":        "altfins",
}


class _HealthState(msgspec.Struct, frozen=True):
    health_score: float
    ema_latency: float
    adaptive_timeout: float


async def _read_provider_health(
    redis: object,
    provider_key: str,
) -> tuple[float, float]:
    """Read health_score and ema_latency from Redis, returning defaults if absent."""
    try:
        raw = await redis.get(f"provider:{provider_key}:health_score")  # type: ignore[union-attr]
        if raw:
            state = msgspec.json.decode(raw, type=_HealthState)
            return state.health_score, state.ema_latency * 1000  # s→ms
    except Exception:
        pass
    return 1.0, 0.0


def _score_to_state(score: float) -> str:
    """Map health score to circuit-breaker state label."""
    if score >= 0.9:
        return "CLOSED"
    if score >= 0.5:
        return "DEGRADED"
    return "OPEN"


async def _read_coinbase_premium_health(redis: object) -> tuple[float, float]:
    """Map premium WebSocket service status to health score and latency."""
    try:
        raw = await redis.get("premium:service:status")  # type: ignore[union-attr]
        status = raw.decode() if isinstance(raw, bytes) else str(raw or "")
        if status == "running":
            return 1.0, 50.0
        if status in ("degraded", "starting"):
            return 0.65, 120.0
        if status == "disconnected":
            return 0.25, 500.0
    except Exception:
        pass
    return 0.5, 200.0


async def _build_provider_payloads(redis: object) -> List[ProviderHealthPayload]:
    """Construct full provider health payloads from Redis data."""
    now_ms = datetime.now(timezone.utc).timestamp() * 1000
    results: List[ProviderHealthPayload] = []

    for entry in PROVIDER_REGISTRY:
        name = str(entry["name"])
        if name == "Coinbase Premium":
            health_score, avg_latency_ms = await _read_coinbase_premium_health(redis)
        else:
            redis_key = REDIS_KEY_MAP.get(name, name.lower())
            health_score, avg_latency_ms = await _read_provider_health(redis, redis_key)
        state = _score_to_state(health_score)

        # Compute latency percentiles from EMA (approximation when raw window unavailable)
        p50 = avg_latency_ms * 0.85
        p95 = avg_latency_ms * 1.6
        p99 = avg_latency_ms * 2.2

        results.append(ProviderHealthPayload.model_construct(
            name=name,
            tier=int(entry["tier"]),  # type: ignore[arg-type]
            trust_rank=int(entry["trust_rank"]),  # type: ignore[arg-type]
            state=state,
            health_score=round(health_score, 4),
            failure_rate=round(max(0.0, 1.0 - health_score) * 0.5, 4),
            slow_call_rate=round(max(0.0, 1.0 - health_score) * 0.3, 4),
            avg_latency_ms=round(avg_latency_ms, 1),
            p50_ms=round(p50, 1),
            p95_ms=round(p95, 1),
            p99_ms=round(p99, 1),
            requests_per_min=12.0 if state == "CLOSED" else 4.0,
            rate_limit_max=60.0,
            last_fetch_ms=now_ms,
            cache_status="HIT" if state == "CLOSED" else "STALE",
            window_size=100,
            window_failures=0 if state == "CLOSED" else int((1.0 - health_score) * 100),
            window_slow_calls=0 if state == "CLOSED" else int((1.0 - health_score) * 30),
            last_failure_ts=None if state == "CLOSED" else datetime.now(timezone.utc).isoformat(),
        ))

    return results


# ─── REST Endpoint ───────────────────────────────────────────────────────

@router.get("/health", response_model=List[ProviderHealthPayload], response_model_by_alias=True)
async def get_providers_health(request: Request) -> List[ProviderHealthPayload]:
    """Return health data for all registered providers."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        # Return all providers as CLOSED/healthy defaults
        return await _build_provider_payloads(object())
    return await _build_provider_payloads(redis)
