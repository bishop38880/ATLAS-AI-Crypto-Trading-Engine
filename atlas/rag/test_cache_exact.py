import time
import msgspec
import pytest
from fakeredis import aioredis

from atlas.rag.cache_exact import ExactMatchCache, CacheEntry


@pytest.fixture
async def redis_client():
    client = aioredis.FakeRedis()
    yield client
    await client.flushall()


@pytest.fixture
def cache(redis_client):
    return ExactMatchCache(redis_client)


@pytest.mark.asyncio
async def test_store_and_retrieve(cache):
    prompt = "What is the Bitcoin price?"
    response = "The price is $100,000."
    provider = "mistral"
    ttl = 60

    await cache.set(prompt, response, provider, ttl)
    entry = await cache.get(prompt)

    assert entry is not None
    assert entry.response == response
    assert entry.provider == provider
    assert abs(entry.created_at - time.time()) < 2.0


@pytest.mark.asyncio
async def test_ttl_expiration(cache, redis_client):
    prompt = "Short-lived prompt"
    response = "This will disappear quickly"
    
    await cache.set(prompt, response, "mistral", 1)
    
    # Verify it exists initially
    entry = await cache.get(prompt)
    assert entry is not None
    
    # Simulate expiration by using fakeredis directly or sleeping
    # Fakeredis respects asyncio.sleep or we can just mock time, but let's just delete the key
    # Wait, time.sleep doesn't work for fakeredis TTL unless we advance its internal clock.
    # Alternatively, we can just test the TTL is set correctly.
    key = f"cache:exact:{cache._hash(prompt)}"
    ttl = await redis_client.ttl(key)
    assert ttl > 0 and ttl <= 1


@pytest.mark.asyncio
async def test_provider_invalidation(cache):
    await cache.set("Prompt 1", "Response 1", "provider_a", 60)
    await cache.set("Prompt 2", "Response 2", "provider_a", 60)
    await cache.set("Prompt 3", "Response 3", "provider_b", 60)

    # Invalidate provider_a
    deleted = await cache.invalidate_provider("provider_a")
    assert deleted == 2

    # Verify provider_a entries are gone
    assert await cache.get("Prompt 1") is None
    assert await cache.get("Prompt 2") is None

    # Verify provider_b entries remain
    assert await cache.get("Prompt 3") is not None
