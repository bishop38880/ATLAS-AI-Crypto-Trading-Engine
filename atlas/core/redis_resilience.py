"""Fail-fast Redis bootstrap with in-process fallback and background reconnect.

TCP connects to a dead Redis host can stall for tens of seconds unless
``socket_connect_timeout`` / ``socket_timeout`` are set aggressively. When the
primary server is unreachable or fails the POLARIS probe, we substitute an in-memory
``fakeredis`` client so the API and pipelines keep serving, and spawn a non-blocking
background task that retries with exponential backoff until Redis is healthy again.
"""

from __future__ import annotations

import asyncio
from typing import Any

import redis.asyncio as redis
from loguru import logger

from atlas.core.startup import _require_redis_server_major_ge_7
from atlas.shared.config import PolarisSettings


class SwappableRedisProxy:
    """Delegates all attributes to the active Redis-compatible async client."""

    __slots__ = ("_inner",)

    def __init__(self, inner: redis.Redis) -> None:  # type: ignore[type-arg]
        self._inner = inner

    @property
    def inner(self) -> redis.Redis:  # type: ignore[type-arg]
        return self._inner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def swap(self, new_client: redis.Redis) -> redis.Redis:  # type: ignore[type-arg]
        """Replace the active client and return the previous one (caller must aclose)."""
        old = self._inner
        self._inner = new_client
        return old

    async def aclose(self) -> None:
        await self._inner.aclose()


class RedisRuntime:
    """Process-scoped Redis handle: live TCP client or fakeredis fallback + reconnect loop."""

    __slots__ = ("proxy", "_fallback", "_shutdown", "_reconnect_task", "started_in_fallback")

    def __init__(
        self,
        proxy: SwappableRedisProxy,
        fallback: redis.Redis,  # type: ignore[type-arg]
        shutdown: asyncio.Event,
        *,
        started_in_fallback: bool,
        reconnect_task: asyncio.Task[None] | None = None,
    ) -> None:
        self.proxy = proxy
        self._fallback = fallback
        self._shutdown = shutdown
        self._reconnect_task = reconnect_task
        self.started_in_fallback = started_in_fallback

    @property
    def using_fallback(self) -> bool:
        return self.proxy.inner is self._fallback

    @property
    def shutdown_event(self) -> asyncio.Event:
        return self._shutdown

    def spawn_reconnect_loop(self, settings: PolarisSettings) -> None:
        """Schedule exponential-backoff reconnect attempts (no-op if already live)."""
        if not self.started_in_fallback:
            return
        if self._reconnect_task is not None:
            return
        self._reconnect_task = asyncio.create_task(
            _reconnect_loop(self, settings),
            name="redis-resilience-reconnect",
        )

    async def aclose(self) -> None:
        self._shutdown.set()
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            await asyncio.gather(self._reconnect_task, return_exceptions=True)
            self._reconnect_task = None

        inner = self.proxy.inner
        await inner.aclose()
        if inner is not self._fallback:
            try:
                await self._fallback.aclose()
            except Exception as exc:
                logger.debug("redis_fallback_second_close_ignored | err={}", str(exc))


def redis_client_kwargs(settings: PolarisSettings) -> dict[str, Any]:
    """Keyword arguments for ``redis.asyncio.Redis.from_url`` — aggressive fail-fast."""
    return {
        "socket_connect_timeout": float(settings.redis_socket_connect_timeout_s),
        "socket_timeout": float(settings.redis_socket_timeout_s),
        "retry_on_timeout": False,
        "decode_responses": False,
        "health_check_interval": 0,
    }


async def _probe_redis_ready(client: redis.Redis, settings: PolarisSettings) -> None:  # type: ignore[type-arg]
    """Same behavioural probe as ``run_fastapi_startup_checks`` / PolarisStartup step 2."""
    _ = settings  # reserved for future per-settings probe tuning
    await client.set("polaris:startup:probe", "1", ex=10)
    val = await client.get("polaris:startup:probe")
    if val not in (b"1", "1"):
        raise RuntimeError("Redis probe failed")
    info_raw = await client.info()
    _require_redis_server_major_ge_7(dict(info_raw))


async def _try_connect_live(settings: PolarisSettings) -> redis.Redis | None:  # type: ignore[type-arg]
    """Return a connected live client or ``None`` if creation or probe fails."""
    kwargs = redis_client_kwargs(settings)
    candidate = redis.from_url(settings.redis_url, **kwargs)
    try:
        await asyncio.wait_for(
            _probe_redis_ready(candidate, settings),
            timeout=float(settings.redis_startup_probe_timeout_s),
        )
        return candidate
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            "redis_live_probe_failed | err={} | action=discard_candidate",
            str(exc),
        )
        await candidate.aclose()
        return None


async def create_redis_runtime(settings: PolarisSettings) -> RedisRuntime:
    """Create proxy + optional background reconnect. Never blocks beyond probe timeout."""
    try:
        import fakeredis.aioredis as fakeredis_aioredis
    except ImportError as exc:  # pragma: no cover - guarded by project deps
        raise RuntimeError(
            "fakeredis is required for Redis degraded-mode fallback; "
            "install the atlas package dependencies (fakeredis).",
        ) from exc

    fallback = fakeredis_aioredis.FakeRedis(decode_responses=False)
    shutdown = asyncio.Event()

    live = await _try_connect_live(settings)
    if live is not None:
        proxy = SwappableRedisProxy(live)
        logger.info(
            "redis_runtime_ready | mode=live | connect_timeout_s={} | socket_timeout_s={}",
            settings.redis_socket_connect_timeout_s,
            settings.redis_socket_timeout_s,
        )
        return RedisRuntime(
            proxy=proxy,
            fallback=fallback,
            shutdown=shutdown,
            started_in_fallback=False,
        )

    logger.error(
        "redis_runtime_degraded | mode=in_memory_fallback | reason=live_unreachable_or_probe_failed",
    )
    proxy = SwappableRedisProxy(fallback)
    runtime = RedisRuntime(
        proxy=proxy,
        fallback=fallback,
        shutdown=shutdown,
        started_in_fallback=True,
    )
    runtime.spawn_reconnect_loop(settings)
    return runtime


async def _reconnect_loop(runtime: RedisRuntime, settings: PolarisSettings) -> None:
    delay_s = float(settings.redis_reconnect_backoff_initial_s)
    max_delay_s = float(settings.redis_reconnect_backoff_max_s)
    shutdown = runtime.shutdown_event
    while not shutdown.is_set():
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=delay_s)
            return
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            raise

        if not runtime.using_fallback:
            return

        live = await _try_connect_live(settings)
        if live is None:
            delay_s = min(delay_s * 2.0, max_delay_s)
            logger.warning(
                "redis_reconnect_backoff | next_retry_s={} | max_s={}",
                delay_s,
                max_delay_s,
            )
            continue

        try:
            old = await runtime.proxy.swap(live)
            await old.aclose()
        except asyncio.CancelledError:
            await live.aclose()
            raise
        except Exception as exc:
            logger.exception("redis_reconnect_swap_failed | err={}", str(exc))
            await live.aclose()
            delay_s = min(delay_s * 2.0, max_delay_s)
            continue

        logger.info("redis_reconnected | mode=live | swapped_from=fallback")
        return


def redis_using_fallback_for_startup(runtime: RedisRuntime | None) -> bool:
    """Whether HTTP startup checks should skip duplicate TCP probe."""
    if runtime is None:
        return False
    return runtime.started_in_fallback
