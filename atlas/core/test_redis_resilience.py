"""Tests for Redis fail-fast bootstrap and in-memory fallback."""

from __future__ import annotations

import asyncio

import pytest

from atlas.core.redis_resilience import (
    RedisRuntime,
    SwappableRedisProxy,
    create_redis_runtime,
    redis_client_kwargs,
    redis_using_fallback_for_startup,
)
from atlas.shared.config import PolarisSettings


def test_redis_client_kwargs_respects_settings() -> None:
    base = PolarisSettings(embed_provider="lmstudio", _env_file=None)
    tuned = base.model_copy(
        update={
            "redis_socket_connect_timeout_s": 2.0,
            "redis_socket_timeout_s": 3.0,
        },
    )
    kw = redis_client_kwargs(tuned)
    assert kw["socket_connect_timeout"] == 2.0
    assert kw["socket_timeout"] == 3.0
    assert kw["retry_on_timeout"] is False
    assert kw["decode_responses"] is False


@pytest.mark.asyncio
async def test_swappable_proxy_forwards_ping() -> None:
    import fakeredis.aioredis

    inner = fakeredis.aioredis.FakeRedis(decode_responses=False)
    proxy = SwappableRedisProxy(inner)
    assert await proxy.ping() is True
    await proxy.aclose()


@pytest.mark.asyncio
async def test_create_redis_runtime_fallback_on_dead_port() -> None:
    """No listener on a spare port — connect/probe must finish within probe timeout."""
    settings = PolarisSettings(
        redis_url="redis://127.0.0.1:63987/0",
        redis_socket_connect_timeout_s=1.0,
        redis_socket_timeout_s=1.0,
        redis_startup_probe_timeout_s=2.0,
        embed_provider="lmstudio",
        _env_file=None,
    )
    t0 = asyncio.get_event_loop().time()
    runtime = await create_redis_runtime(settings)
    elapsed = asyncio.get_event_loop().time() - t0
    assert elapsed < 8.0, "initialization must not approach default ~30s TCP stalls"
    assert runtime.started_in_fallback is True
    assert runtime.using_fallback is True
    assert redis_using_fallback_for_startup(runtime) is True
    assert await runtime.proxy.ping() is True
    await runtime.aclose()


@pytest.mark.asyncio
async def test_redis_runtime_aclose_when_live_never_used_fallback() -> None:
    import fakeredis.aioredis

    fb = fakeredis.aioredis.FakeRedis(decode_responses=False)
    shutdown = asyncio.Event()
    proxy = SwappableRedisProxy(fb)
    runtime = RedisRuntime(
        proxy=proxy,
        fallback=fb,
        shutdown=shutdown,
        started_in_fallback=True,
    )
    await runtime.aclose()
