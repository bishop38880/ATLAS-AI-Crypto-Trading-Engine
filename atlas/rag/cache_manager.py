"""Unified Cache Manager for ATLAS RAG.

Combines Tier 1 (Exact) and Tier 2 (Semantic) caches.
Provides a unified interface for lookup, storage, and invalidation.
"""

import asyncio

from loguru import logger

from atlas.rag.cache_exact import CacheEntry, ExactMatchCache
from atlas.rag.cache_semantic import SemanticCache


class CachedResponse:
    """Unified cache response containing tier information."""

    def __init__(self, entry: CacheEntry, tier: str) -> None:
        """Initialize with the matched entry and the hit tier."""
        self.entry = entry
        self.tier = tier


class CacheManager:
    """Manages exact and semantic cache tiers."""

    def __init__(self, exact_cache: ExactMatchCache, semantic_cache: SemanticCache) -> None:
        """Initialize with configured Tier 1 and Tier 2 caches."""
        self._exact = exact_cache
        self._semantic = semantic_cache

    async def lookup(self, prompt: str, category: str) -> CachedResponse | None:
        """Look up a prompt in the caches sequentially."""
        # Tier 1: Exact match (fast)
        exact_entry = await self._exact.get(prompt)
        if exact_entry:
            logger.info("Cache hit (Tier 1: Exact) for category {}", category)
            return CachedResponse(entry=exact_entry, tier="exact")

        # Tier 2: Semantic match (slower, fallback)
        semantic_entry = await self._semantic.search(prompt, category)
        if semantic_entry:
            logger.info("Cache hit (Tier 2: Semantic) for category {}", category)
            return CachedResponse(entry=semantic_entry, tier="semantic")

        logger.debug("Cache miss for category {}", category)
        return None

    async def store(self, prompt: str, response: str, provider: str, category: str, ttl: int) -> None:
        """Store the response in both exact and semantic caches concurrently."""
        await asyncio.gather(
            self._exact.set(prompt, response, provider, ttl),
            self._semantic.store(prompt, response, provider, category, ttl),
        )

    async def invalidate_provider(self, provider: str) -> None:
        """Invalidate all cache entries for a specific provider concurrently."""
        logger.info("Invalidating cache for provider: {}", provider)
        deleted_exact, _ = await asyncio.gather(
            self._exact.invalidate_provider(provider),
            self._semantic.invalidate_provider(provider),
        )
        logger.info("Invalidation complete for {} (exact entries removed: {})", provider, deleted_exact)
