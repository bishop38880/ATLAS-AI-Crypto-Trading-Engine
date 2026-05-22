"""Tests for provider manual health test endpoints (circuit-breaker routed)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from atlas.api.routes.health import ProviderTestResult, router


@pytest.fixture()
def app() -> FastAPI:
    """Create a minimal FastAPI test app with the health router."""
    _app = FastAPI()
    _app.include_router(router)
    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Async HTTP client wired to ASGI."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class _FakePool:
    """Mimics an HTTP pool with async ``health_check``."""

    def __init__(self, ok: bool = True, latency: float = 0.01) -> None:
        self._ok = ok
        self._latency = latency

    async def health_check(self) -> bool:
        await asyncio.sleep(self._latency)
        return self._ok


class _ExplodingPool:
    """Always raises from ``health_check``."""

    async def health_check(self) -> bool:
        raise RuntimeError("ping_failed")


async def _async_breaker_call_side_effect(
    fn: object,
    crit: object,
    inst: object,
) -> object:
    """Match ``ProviderCircuitBreaker.call`` — await the wrapped coroutine."""
    assert callable(fn)
    return await fn(inst)  # type: ignore[misc]


@pytest.mark.anyio
async def test_single_provider_test_returns_schema(client: AsyncClient) -> None:
    """POST single test returns ProviderTestResult field set with int latency."""
    fake = _FakePool(ok=True, latency=0.01)
    with (
        patch("atlas.api.routes.health.get_provider", return_value=fake),
        patch("atlas.api.routes.health.get_circuit_breaker") as mock_cb,
    ):
        breaker = MagicMock()
        breaker.current_state = "closed"
        breaker.hydrate_state_from_redis = AsyncMock()
        breaker.call = AsyncMock(side_effect=_async_breaker_call_side_effect)
        mock_cb.return_value = breaker

        resp = await client.post("/api/v1/providers/Coinalyze/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "Coinalyze"
    assert body["status"] == "healthy"
    assert isinstance(body["latency_ms"], int)
    assert body["error"] is None
    assert body["circuit_state"] == "CLOSED"
    assert isinstance(body["tested_at"], str)


@pytest.mark.anyio
async def test_unknown_provider_returns_404(client: AsyncClient) -> None:
    """Unknown provider slug returns 404."""
    resp = await client.post("/api/v1/providers/NonExistentProvider/test")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_single_provider_test_trips_breaker_on_failure(client: AsyncClient) -> None:
    """A failing ping exercises the breaker ``call`` failure path."""
    fake = _ExplodingPool()
    failure_hook = AsyncMock()

    async def _wrap_call(fn: object, crit: object, inst: object) -> object:
        try:
            return await _async_breaker_call_side_effect(fn, crit, inst)
        except Exception:
            await failure_hook()
            raise

    with (
        patch("atlas.api.routes.health.get_provider", return_value=fake),
        patch("atlas.api.routes.health.get_circuit_breaker") as mock_cb,
    ):
        breaker = MagicMock()
        breaker.current_state = "closed"
        breaker.hydrate_state_from_redis = AsyncMock()
        breaker.call = AsyncMock(side_effect=_wrap_call)
        mock_cb.return_value = breaker

        resp = await client.post("/api/v1/providers/Coinalyze/test")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error"] is not None
    assert failure_hook.await_count == 1


@pytest.mark.anyio
async def test_test_all_uses_gather_not_sequential(client: AsyncClient) -> None:
    """Three slow checks finish in wall time consistent with concurrency."""
    tiny_registry = [
        {"name": "Alpha", "tier": 1, "trust_rank": 1},
        {"name": "Beta", "tier": 1, "trust_rank": 2},
        {"name": "Gamma", "tier": 1, "trust_rank": 3},
    ]
    tiny_map = {"Alpha": "alpha", "Beta": "beta", "Gamma": "gamma"}

    fake = _FakePool(ok=True, latency=0.10)
    started: list[float] = []

    async def _tracked_call(fn: object, crit: object, inst: object) -> object:
        started.append(time.perf_counter())
        return await _async_breaker_call_side_effect(fn, crit, inst)

    with (
        patch("atlas.api.routes.health.PROVIDER_REGISTRY", tiny_registry),
        patch("atlas.api.routes.health.REDIS_KEY_MAP", tiny_map),
        patch("atlas.api.routes.health.get_provider", return_value=fake),
        patch("atlas.api.routes.health.get_circuit_breaker") as mock_cb,
    ):
        breaker = MagicMock()
        breaker.current_state = "closed"
        breaker.hydrate_state_from_redis = AsyncMock()
        breaker.call = AsyncMock(side_effect=_tracked_call)
        mock_cb.return_value = breaker

        t0 = time.perf_counter()
        resp = await client.post("/api/v1/providers/test-all")
        wall = time.perf_counter() - t0

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    assert wall < 0.38
    started.sort()
    if len(started) >= 2:
        min_gap = min(started[i + 1] - started[i] for i in range(len(started) - 1))
        assert min_gap < 0.04


@pytest.mark.anyio
async def test_test_all_returns_partial_results_on_exception(client: AsyncClient) -> None:
    """Gather keeps HTTP 200 — one exploding provider yields an error row."""
    tiny_registry = [
        {"name": "Alpha", "tier": 1, "trust_rank": 1},
        {"name": "Beta", "tier": 1, "trust_rank": 2},
    ]
    tiny_map = {"Alpha": "alpha", "Beta": "beta"}

    pools: dict[str, object] = {
        "alpha": _FakePool(ok=True, latency=0.001),
        "beta": _ExplodingPool(),
    }

    def _pool_for_registry_key(key: str) -> object | None:
        return pools[key]

    with (
        patch("atlas.api.routes.health.PROVIDER_REGISTRY", tiny_registry),
        patch("atlas.api.routes.health.REDIS_KEY_MAP", tiny_map),
        patch(
            "atlas.api.routes.health.get_provider",
            side_effect=lambda k: _pool_for_registry_key(k),
        ),
        patch("atlas.api.routes.health.get_circuit_breaker") as mock_cb,
    ):
        breaker = MagicMock()
        breaker.current_state = "closed"
        breaker.hydrate_state_from_redis = AsyncMock()
        breaker.call = AsyncMock(side_effect=_async_breaker_call_side_effect)
        mock_cb.return_value = breaker

        resp = await client.post("/api/v1/providers/test-all")

    assert resp.status_code == 200
    payload = resp.json()
    beta_row = next(r for r in payload if r["provider"] == "Beta")
    assert beta_row["status"] == "error"


@pytest.mark.anyio
async def test_response_schema_matches_msgspec_struct(client: AsyncClient) -> None:
    """Decode confirms ``latency_ms`` is int wire type."""
    fake = _FakePool(ok=False, latency=0.005)
    with (
        patch("atlas.api.routes.health.get_provider", return_value=fake),
        patch("atlas.api.routes.health.get_circuit_breaker") as mock_cb,
    ):
        breaker = MagicMock()
        breaker.current_state = "degraded"
        breaker.hydrate_state_from_redis = AsyncMock()
        breaker.call = AsyncMock(side_effect=_async_breaker_call_side_effect)
        mock_cb.return_value = breaker

        resp = await client.post("/api/v1/providers/Pyth/test")

    assert resp.status_code == 200
    parsed = msgspec.json.decode(resp.content, type=ProviderTestResult)
    assert isinstance(parsed.latency_ms, int)
    assert parsed.status == "degraded"
    assert parsed.circuit_state == "DEGRADED"
