from unittest.mock import AsyncMock

import pytest

from atlas.rag.cache_exact import CacheEntry
from atlas.rag.cache_manager import CacheManager


@pytest.fixture
def exact_cache():
    mock = AsyncMock()
    mock.get.return_value = None
    mock.invalidate_provider.return_value = 0
    return mock


@pytest.fixture
def semantic_cache():
    mock = AsyncMock()
    mock.search.return_value = None
    return mock


@pytest.fixture
def cache_manager(exact_cache, semantic_cache):
    return CacheManager(exact_cache, semantic_cache)


@pytest.mark.asyncio
async def test_tier_1_hit_returns_without_querying_tier_2(cache_manager, exact_cache, semantic_cache):
    entry = CacheEntry(response="Exact match", created_at=123.0, provider="mistral")
    exact_cache.get.return_value = entry

    result = await cache_manager.lookup("Prompt", "sentiment")

    assert result is not None
    assert result.tier == "exact"
    assert result.entry.response == "Exact match"
    
    # Verify tier 2 was not queried
    semantic_cache.search.assert_not_called()


@pytest.mark.asyncio
async def test_tier_2_hit_after_tier_1_miss(cache_manager, exact_cache, semantic_cache):
    entry = CacheEntry(response="Semantic match", created_at=123.0, provider="mistral")
    semantic_cache.search.return_value = entry

    result = await cache_manager.lookup("Prompt", "sentiment")

    assert result is not None
    assert result.tier == "semantic"
    assert result.entry.response == "Semantic match"
    
    # Verify both tiers were queried
    exact_cache.get.assert_called_once_with("Prompt")
    semantic_cache.search.assert_called_once_with("Prompt", "sentiment")


@pytest.mark.asyncio
async def test_cache_miss_both_tiers(cache_manager, exact_cache, semantic_cache):
    result = await cache_manager.lookup("Prompt", "sentiment")
    
    assert result is None
    exact_cache.get.assert_called_once_with("Prompt")
    semantic_cache.search.assert_called_once_with("Prompt", "sentiment")


@pytest.mark.asyncio
async def test_store_writes_to_both_tiers(cache_manager, exact_cache, semantic_cache):
    await cache_manager.store("Prompt", "Response", "mistral", "sentiment", 60)
    
    exact_cache.set.assert_called_once_with("Prompt", "Response", "mistral", 60)
    semantic_cache.store.assert_called_once_with("Prompt", "Response", "mistral", "sentiment", 60)


@pytest.mark.asyncio
async def test_provider_invalidation_clears_both_tiers(cache_manager, exact_cache, semantic_cache):
    await cache_manager.invalidate_provider("mistral")
    
    exact_cache.invalidate_provider.assert_called_once_with("mistral")
    semantic_cache.invalidate_provider.assert_called_once_with("mistral")
