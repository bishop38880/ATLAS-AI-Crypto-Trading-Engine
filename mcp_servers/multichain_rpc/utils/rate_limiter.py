"""Redis ZSET sliding-window rate limiter.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Implements a strict distributed sliding-window rate limiter using
``redis.asyncio`` Sorted Sets (ZSETs).  Each request timestamp is
stored as a ZSET member; expired entries are pruned with
``ZREMRANGEBYSCORE``; and ``ZCARD`` checks capacity before permitting.

This is NOT a naive ``asyncio.sleep()`` loop — it provides exact
per-second windowing suitable for Etherscan's strict 5 req/sec limit
and Bitcoin Core's I/O-sensitive RPC endpoint.

Fallback: If Redis is unavailable, the limiter degrades to a local
``collections.deque``-based window.  This is non-distributed but
preserves correctness for single-process MCP servers.
"""

from __future__ import annotations

import time
from collections import deque

import asyncio
from typing import Any

from loguru import logger

try:
    import redis.asyncio as redis_async
    _REDIS_AVAILABLE = True
except ImportError:
    redis_async = None  # type: ignore
    _REDIS_AVAILABLE = False


# ─────────────────────── Constants ───────────────────────────────────────────

_WINDOW_SECONDS: float = 1.0
_DEFAULT_MAX_REQUESTS: int = 5
_REDIS_TIMEOUT_S: float = 2.0


# ─────────────────────── Redis ZSET Limiter ──────────────────────────────────


class RedisSlidingWindowLimiter:
    """Distributed sliding-window rate limiter backed by Redis ZSETs.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.

    Each provider gets its own ZSET key.  Request timestamps are added
    via ``ZADD``; stale entries are removed via ``ZREMRANGEBYSCORE``;
    and ``ZCARD`` determines whether capacity remains.

    Args:
        redis_url: Redis connection string.
        key_prefix: ZSET key prefix (e.g. 'ratelimit:etherscan').
        max_requests: Maximum requests per window.
        window_seconds: Sliding window duration.
    """

    def __init__(
        self,
        redis_url: str,
        key_prefix: str,
        max_requests: int = _DEFAULT_MAX_REQUESTS,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self._redis_url = redis_url
        self._key = f"{key_prefix}:sliding_window"
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._client: Any | None = None
        self._fallback = LocalSlidingWindowLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
        )

    async def _get_client(self) -> Any:
        """Lazy-initialise the Redis client singleton."""
        if self._client is None:
            if not _REDIS_AVAILABLE:
                return None
            try:
                self._client = redis_async.from_url( # type: ignore
                    self._redis_url,
                    decode_responses=False,
                )
            except asyncio.CancelledError:
                raise  # ALWAYS re-raise
            except Exception as exc:
                logger.warning(
                    "Redis connection failed, using local fallback | err={}",
                    exc,
                )
                return None
        return self._client

    async def acquire(self) -> None:
        """Block until a request slot is available.

        Uses Redis ZSET operations: ZREMRANGEBYSCORE → ZCARD → ZADD.
        Falls back to local deque if Redis is unreachable.
        """
        client = await self._get_client()
        if client is None:
            await self._fallback.acquire()
            return
        try:
            await self._acquire_redis(client)
        except asyncio.CancelledError:
            raise  # ALWAYS re-raise
        except Exception as exc:
            logger.warning(
                "Redis rate limiter error, falling back to local | err={}",
                exc,
            )
            await self._fallback.acquire()

    async def _acquire_redis(self, client: Any) -> None:
        """Core Redis ZSET sliding-window logic."""
        while True:
            now = time.time()
            window_start = now - self._window_seconds

            pipe = client.pipeline(transaction=True)
            pipe.zremrangebyscore(self._key, "-inf", window_start)
            pipe.zcard(self._key)
            results = await asyncio.wait_for(
                pipe.execute(),
                timeout=_REDIS_TIMEOUT_S,
            )

            current_count: int = results[1]

            if current_count < self._max_requests:
                await self._add_request_timestamp(client, now)
                return

            await asyncio.sleep(0.05)

    async def _add_request_timestamp(
        self, client: Any, timestamp: float,
    ) -> None:
        """Add a request timestamp to the ZSET."""
        member = f"{timestamp}:{id(self)}"
        await asyncio.wait_for(
            client.zadd(self._key, {member: timestamp}),
            timeout=_REDIS_TIMEOUT_S,
        )
        await asyncio.wait_for(
            client.expire(self._key, int(self._window_seconds) + 2),
            timeout=_REDIS_TIMEOUT_S,
        )

    async def close(self) -> None:
        """Close the Redis connection."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


# ─────────────────────── Local Fallback Limiter ──────────────────────────────


class LocalSlidingWindowLimiter:
    """In-process sliding-window rate limiter using ``collections.deque``.

    Non-distributed fallback for single-process MCP servers.
    Provides the same interface as ``RedisSlidingWindowLimiter``.

    Args:
        max_requests: Maximum requests per window.
        window_seconds: Sliding window duration.
    """

    def __init__(
        self,
        max_requests: int = _DEFAULT_MAX_REQUESTS,
        window_seconds: float = _WINDOW_SECONDS,
    ) -> None:
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        """Block until a request slot is available."""
        while True:
            now = time.time()
            self._prune_expired(now)

            if len(self._timestamps) < self._max_requests:
                self._timestamps.append(now)
                return

            await asyncio.sleep(0.05)

    def _prune_expired(self, now: float) -> None:
        """Remove timestamps older than the window."""
        cutoff = now - self._window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    async def close(self) -> None:
        """No-op for local limiter — interface compatibility."""
