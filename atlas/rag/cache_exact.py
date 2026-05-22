"""Exact Match Cache (Tier 1) for ATLAS RAG.

Provides sub-millisecond cache lookups for exactly matching prompts.
Backed by Redis, using msgspec for efficient serialization.
"""

import hashlib
import time

import msgspec
import redis.asyncio as redis
from loguru import logger


class CacheEntry(msgspec.Struct):
    """Schema for cached responses."""

    response: str
    created_at: float
    provider: str


class ExactMatchCache:
    """Tier 1 cache using exact string matching via SHA-256."""

    def __init__(self, redis_client: redis.Redis) -> None:
        """Initialize with a connected Redis client."""
        self._redis = redis_client

    def _hash(self, prompt: str) -> str:
        """Compute the SHA-256 hash of the prompt."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    async def get(self, prompt: str) -> CacheEntry | None:
        """Retrieve a cached response for the exact prompt."""
        key = f"cache:exact:{self._hash(prompt)}"
        data = await self._redis.get(key)
        if data is None:
            return None

        try:
            return msgspec.json.decode(data, type=CacheEntry)
        except msgspec.DecodeError as e:
            logger.error("Failed to decode cache entry for {}: {}", key, e)
            return None

    async def set(self, prompt: str, response: str, provider: str, ttl_seconds: int) -> None:
        """Store a response in the exact match cache."""
        key = f"cache:exact:{self._hash(prompt)}"
        entry = CacheEntry(response=response, created_at=time.time(), provider=provider)
        data = msgspec.json.encode(entry)

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, data, ex=ttl_seconds)
            pipe.sadd(f"cache:provider:{provider}", key)
            pipe.expire(f"cache:provider:{provider}", ttl_seconds)
            await pipe.execute()

    async def invalidate_provider(self, provider: str) -> int:
        """Delete all cache entries associated with a provider."""
        provider_key = f"cache:provider:{provider}"
        keys = await self._redis.smembers(provider_key)  # type: ignore
        if not keys:
            return 0

        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.delete(*keys)
            pipe.delete(provider_key)
            await pipe.execute()

        return len(keys)
