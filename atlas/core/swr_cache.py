"""Stale-While-Revalidate Cache."""

import asyncio
import time
from typing import Any, Awaitable, Callable, Set

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel

class CacheResult(BaseModel, frozen=True):
    """Result from a cache lookup.
    
    Attributes:
        data: The cached or freshly fetched data.
        is_stale: True if returned from stale cache, False if fresh.
        age_seconds: How old the cached data is (0.0 if fresh).
    """
    data: Any
    is_stale: bool
    age_seconds: float

class StaleWhileRevalidateCache:
    """A cache that returns stale data immediately while refreshing in background.
    
    Uses msgspec for serialization. Background tasks are strictly tracked
    to prevent fire-and-forget memory leaks.
    """

    def __init__(self, redis_client: redis_async.Redis):
        """Initialize the SWR Cache.
        
        Args:
            redis_client: Async Redis connection.
        """
        self._redis = redis_client
        self._background_tasks: Set[asyncio.Task[str]] = set()
        self._pending_refreshes: Set[str] = set()

    async def get_or_fetch(
        self, key: str, fetcher: Callable[[], Awaitable[Any]], ttl: int, stale_factor: float
    ) -> CacheResult:
        """Get data from cache or fetch it synchronously if missing/expired.
        
        If data is within the stale window, returns it and triggers a background refresh.
        
        Args:
            key: Cache key.
            fetcher: Async callable to fetch fresh data.
            ttl: Base fresh TTL in seconds.
            stale_factor: Multiplier for stale window (e.g. 1.5 = stale for 0.5*ttl longer).
            
        Returns:
            CacheResult containing the data and its status.
        """
        raw = await self._redis.get(key)
        now = time.time()

        if raw:
            try:
                entry = msgspec.json.decode(raw)
                fresh_until = entry.get("fresh_until", 0)
                stale_until = entry.get("stale_until", 0)
                fetched_at = entry.get("fetched_at", now)
                age = now - fetched_at

                if now < fresh_until:
                    return CacheResult(data=entry["data"], is_stale=False, age_seconds=age)
                elif now < stale_until:
                    self._trigger_background_refresh(key, fetcher, ttl, stale_factor)
                    return CacheResult(data=entry["data"], is_stale=True, age_seconds=age)
            except Exception as e:
                logger.error("Failed to decode cache entry for {}, treating as miss: {}", key, str(e))

        return await self._fetch_sync(key, fetcher, ttl, stale_factor)

    async def _fetch_sync(
        self, key: str, fetcher: Callable[[], Awaitable[Any]], ttl: int, stale_factor: float
    ) -> CacheResult:
        """Fetch data synchronously and update cache."""
        data = await fetcher()
        now = time.time()
        
        entry = {
            "data": data,
            "fresh_until": now + ttl,
            "stale_until": now + (ttl * stale_factor),
            "fetched_at": now,
        }
        await self._redis.set(key, msgspec.json.encode(entry))
        return CacheResult(data=data, is_stale=False, age_seconds=0.0)

    _MAX_CONCURRENT_REFRESHES: int = 20

    def _trigger_background_refresh(
        self, key: str, fetcher: Callable[[], Awaitable[Any]], ttl: int, stale_factor: float
    ) -> None:
        """Trigger a background task to refresh stale data, if not already pending.

        Capped at _MAX_CONCURRENT_REFRESHES to prevent task pile-up.
        """
        if key in self._pending_refreshes:
            return
        if len(self._background_tasks) >= self._MAX_CONCURRENT_REFRESHES:
            logger.warning("swr_cache_max_concurrent_refreshes | key={}", key)
            return

        self._pending_refreshes.add(key)

        task = asyncio.create_task(self._bg_fetch_task(key, fetcher, ttl, stale_factor))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_bg_fetch_done)

    async def _bg_fetch_task(
        self, key: str, fetcher: Callable[[], Awaitable[Any]], ttl: int, stale_factor: float
    ) -> str:
        """Background wrapper for syncing fetch.
        
        Returns the key to allow cleanup in the callback.
        """
        try:
            await self._fetch_sync(key, fetcher, ttl, stale_factor)
        finally:
            self._pending_refreshes.discard(key)
        return key

    def _on_bg_fetch_done(self, task: asyncio.Task[str]) -> None:
        """Cleanup tracking for a completed background refresh task."""
        self._background_tasks.discard(task)
        try:
            task.result()
        except Exception as e:
            logger.error("Background fetch task failed: {}", str(e))
