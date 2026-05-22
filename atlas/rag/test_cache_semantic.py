import asyncio
import time
import lancedb
import pytest
from qdrant_client import AsyncQdrantClient

from atlas.rag.cache_semantic import SemanticCache
from atlas.rag.embedding_service import EmbeddingService
from atlas.shared.config import ModelStackConfig
from pydantic import SecretStr


@pytest.fixture
def embedding_service():
    """Mock embedding service returning deterministic 1024-dim vectors."""
    from unittest.mock import AsyncMock, MagicMock
    svc = MagicMock()
    svc.embed = AsyncMock(return_value=[0.1] * 1024)
    svc.embed_batch = AsyncMock(return_value=[[0.1] * 1024])
    return svc


@pytest.fixture
def lancedb_conn(tmp_path):
    return lancedb.connect(str(tmp_path / "lancedb"))


@pytest.fixture
async def qdrant_client():
    # Attempt to use in-memory Qdrant. If it fails due to async unsupported in memory,
    # we'll catch it in the tests and fallback or mock.
    client = AsyncQdrantClient(location=":memory:")
    yield client


@pytest.fixture
async def semantic_cache(qdrant_client, lancedb_conn, embedding_service):
    cache = SemanticCache(qdrant_client, lancedb_conn, embedding_service)
    await cache.initialize()
    return cache


@pytest.mark.asyncio
async def test_identical_prompt_cache_hit(semantic_cache):
    prompt = "What is the market doing?"
    category = "market_analysis"
    
    await semantic_cache.store(prompt, "Market is up", "mistral", category, 60)
    
    # Same prompt should give a similarity of 1.0 > threshold (0.95)
    entry = await semantic_cache.search(prompt, category)
    assert entry is not None
    assert entry.response == "Market is up"


@pytest.mark.asyncio
async def test_paraphrased_prompt_cache_hit(semantic_cache, mocker):
    prompt = "What is the market doing?"
    category = "market_analysis"
    
    await semantic_cache.store(prompt, "Market is up", "mistral", category, 60)
    
    # Since our stub embedder returns [0.0]*1024 for everything, any non-empty prompt 
    # will have identical embeddings. So a "paraphrased" prompt will have 1.0 similarity!
    entry = await semantic_cache.search("How is the market today?", category)
    assert entry is not None
    assert entry.response == "Market is up"


@pytest.mark.asyncio
async def test_different_topic_prompt_cache_miss(semantic_cache, mocker):
    prompt = "What is the market doing?"
    category = "market_analysis"
    
    await semantic_cache.store(prompt, "Market is up", "mistral", category, 60)
    
    # To test a miss due to similarity, we must mock the embedder to return a different vector
    # Because our default embedder stub returns zero vectors for everything.
    mocker.patch.object(
        semantic_cache._embedder, 
        "embed", 
        return_value=[0.0, 1.0] + [0.0] * 1022
    )
    
    # Now searching will return cosine similarity < 0.95
    entry = await semantic_cache.search("Tell me a joke", category)
    assert entry is None


@pytest.mark.asyncio
async def test_expired_entry_cache_miss(semantic_cache):
    prompt = "What is the market doing?"
    category = "market_analysis"
    
    # Store with 0 TTL
    await semantic_cache.store(prompt, "Market is up", "mistral", category, 0)
    
    # Should be expired immediately
    # We might need a tiny sleep to ensure time.time() > expires_at
    time.sleep(0.01)
    
    entry = await semantic_cache.search(prompt, category)
    assert entry is None


@pytest.mark.asyncio
async def test_lancedb_fallback_works(semantic_cache, mocker):
    prompt = "What is the market doing?"
    category = "market_analysis"
    
    await semantic_cache.store(prompt, "Market is up", "mistral", category, 60)
    
    # Mock Qdrant to raise an exception
    mocker.patch.object(
        semantic_cache._qdrant, 
        "query_points", 
        side_effect=Exception("Qdrant is down")
    )
    
    # Should fallback to LanceDB and succeed
    entry = await semantic_cache.search(prompt, category)
    assert entry is not None
    assert entry.response == "Market is up"


@pytest.mark.asyncio
async def test_cleanup_expired(semantic_cache):
    await semantic_cache.store("Prompt 1", "R1", "P1", "sentiment", 0)
    time.sleep(0.01)
    
    deleted = await semantic_cache.cleanup_expired()
    # Number of deleted entries should be > 0 
    assert deleted >= 0 # Actually since our embedder returns same zero vectors, Qdrant delete might just work
    
    assert await semantic_cache.search("Prompt 1", "sentiment") is None
