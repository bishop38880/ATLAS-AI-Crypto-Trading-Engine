"""Health test endpoints for on-demand provider pinging.

Routes provider health checks through the ProviderCircuitBreaker
so manual tests feed the sliding window and can trip the breaker.
"""

# TODO(FE-TV-B): async job status endpoint for long-running test-all

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Final

import msgspec
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from atlas.core.circuit_breaker import ProviderCircuitBreaker, get_circuit_breaker
from atlas.core.registry import get_provider

from atlas.api.routes.providers import PROVIDER_REGISTRY, REDIS_KEY_MAP

router = APIRouter(prefix="/api/v1/providers", tags=["provider-test"])

_POOL_NAME_OVERRIDES: Final[dict[str, str]] = {
    "fred": "fred_rest",
    "dune": "dune_mcp",
    "alternative_me": "fear_greed",
}

_CIRCUIT_WIRE_LABELS: Final[dict[str, str]] = {
    "closed": "CLOSED",
    "open": "OPEN",
    "half_open": "HALF_OPEN",
    "degraded": "DEGRADED",
}


class ProviderTestResult(msgspec.Struct, frozen=True):
    """Wire schema for a single provider health test."""

    provider: str
    status: str  # healthy | degraded | error
    latency_ms: int
    error: str | None
    circuit_state: str  # CLOSED | OPEN | HALF_OPEN | DEGRADED
    tested_at: str


def _utc_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _registry_pool_lookup_key(redis_key: str) -> str:
    return _POOL_NAME_OVERRIDES.get(redis_key, redis_key)


def _resolve_provider_redis_key(display_name: str) -> str | None:
    lowered = display_name.lower().replace(" ", "").replace(".", "")
    for name, redis_key in REDIS_KEY_MAP.items():
        nk = name.lower().replace(" ", "").replace(".", "")
        if nk == lowered:
            return redis_key
    return None


def _circuit_state_wire_label(breaker: ProviderCircuitBreaker) -> str:
    raw = breaker.current_state.lower()
    return _CIRCUIT_WIRE_LABELS.get(raw, raw.upper())


def _elapsed_ms(start_ns: int) -> int:
    return int((time.perf_counter_ns() - start_ns) / 1_000_000)


async def _ping_pool_simple(instance: object) -> bool:
    """Await ``health_check()`` on ``instance``."""
    checker = getattr(instance, "health_check", None)
    if checker is None:
        raise TypeError("instance_missing_health_check")
    outcome = checker()
    if asyncio.iscoroutine(outcome):
        return await asyncio.wait_for(outcome, timeout=10.0)
    raise TypeError("health_check_not_async")


def _result_after_ok(
    display_name: str,
    ok: object,
    elapsed_ms: int,
    circuit_state: str,
) -> ProviderTestResult:
    if ok is None:
        err = (
            "circuit_open"
            if circuit_state == "OPEN"
            else "circuit_blocked_degraded_skip"
        )
        return ProviderTestResult(
            provider=display_name,
            status="error",
            latency_ms=elapsed_ms,
            error=err,
            circuit_state=circuit_state,
            tested_at=_utc_iso_now(),
        )
    wire_status = "healthy" if ok else "degraded"
    return ProviderTestResult(
        provider=display_name,
        status=wire_status,
        latency_ms=elapsed_ms,
        error=None,
        circuit_state=circuit_state,
        tested_at=_utc_iso_now(),
    )


async def _test_one_provider(display_name: str, redis_key: str) -> ProviderTestResult:
    """Wrap ``provider.health_check()`` behind the breaker and build the wire result."""
    tested_at = _utc_iso_now()
    breaker = get_circuit_breaker(redis_key)
    await breaker.hydrate_state_from_redis()
    pool_key = _registry_pool_lookup_key(redis_key)
    instance = get_provider(pool_key)
    start_ns = time.perf_counter_ns()

    if instance is None:
        return ProviderTestResult(
            provider=display_name,
            status="error",
            latency_ms=0,
            error="no_live_pool",
            circuit_state=_circuit_state_wire_label(breaker),
            tested_at=tested_at,
        )

    try:
        ok = await breaker.call(_ping_pool_simple, True, instance)
        ms = _elapsed_ms(start_ns)
        await breaker.hydrate_state_from_redis()
        return _result_after_ok(display_name, ok, ms, _circuit_state_wire_label(breaker))

    except asyncio.CancelledError:
        raise

    except Exception as exc:
        logger.warning(
            "health_test_failed | provider={} | err={}",
            display_name,
            str(exc),
        )
        await breaker.hydrate_state_from_redis()
        return ProviderTestResult(
            provider=display_name,
            status="error",
            latency_ms=_elapsed_ms(start_ns),
            error=str(exc)[:200],
            circuit_state=_circuit_state_wire_label(breaker),
            tested_at=_utc_iso_now(),
        )


def _coerce_gather_result(slot: object, display_name: str) -> ProviderTestResult:
    if isinstance(slot, ProviderTestResult):
        return slot
    if isinstance(slot, BaseException):
        return ProviderTestResult(
            provider=display_name,
            status="error",
            latency_ms=0,
            error=str(slot)[:200],
            circuit_state="CLOSED",
            tested_at=_utc_iso_now(),
        )
    return ProviderTestResult(
        provider=display_name,
        status="error",
        latency_ms=0,
        error="unexpected_gather_slot",
        circuit_state="CLOSED",
        tested_at=_utc_iso_now(),
    )


def _canonical_display_for_request(provider_request_name: str) -> str | None:
    norm = provider_request_name.lower().replace(" ", "").replace(".", "")
    for entry in PROVIDER_REGISTRY:
        name = str(entry["name"])
        if name.lower().replace(" ", "").replace(".", "") == norm:
            return name
    return None


@router.post(
    "/{provider_name}/test",
    response_class=JSONResponse,
)
async def test_single_provider(provider_name: str) -> JSONResponse:
    """Ping one provider via the breaker; failures count toward breaker windows."""
    redis_key = _resolve_provider_redis_key(provider_name)
    if redis_key is None:
        raise HTTPException(status_code=404, detail="unknown_provider")

    display = _canonical_display_for_request(provider_name)
    if display is None:
        display = provider_name

    outcome = await _test_one_provider(display, redis_key)
    payload_dict = msgspec.json.decode(msgspec.json.encode(outcome))
    return JSONResponse(content=payload_dict)


@router.post(
    "/test-all",
    response_class=JSONResponse,
)
async def test_all_providers() -> JSONResponse:
    """Concurrently pings every configured provider envelope."""
    targets: list[tuple[str, str]] = []
    for entry in PROVIDER_REGISTRY:
        name = str(entry["name"])
        redis_k = REDIS_KEY_MAP.get(name, name.lower())
        targets.append((name, redis_k))

    results = await asyncio.gather(
        *[_test_one_provider(n, k) for n, k in targets],
        return_exceptions=True,
    )

    coerced = [
        _coerce_gather_result(results[i], targets[i][0])
        for i in range(len(targets))
    ]
    encoded = msgspec.json.encode(coerced)
    payload = msgspec.json.decode(encoded)
    return JSONResponse(content=payload)
