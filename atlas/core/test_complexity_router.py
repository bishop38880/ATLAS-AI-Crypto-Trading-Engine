"""Tests for Complexity Router."""

import pytest

from pydantic import SecretStr

from atlas.core.complexity_router import ComplexityRouter
from atlas.core.llm_client import BaseLLMClient, LLMResponse
from atlas.rag.cache_exact import CacheEntry, ExactMatchCache
from atlas.rag.cache_manager import CacheManager, CachedResponse
from atlas.shared.config import ModelStackConfig


class MockLLMClient(BaseLLMClient):
    def __init__(self, name: str):
        self.name = name

    async def complete(self, prompt: str, max_tokens: int = 1000) -> LLMResponse:
        return LLMResponse(
            text=f"Response from {self.name}",
            reasoning=None,
            model=self.name,
            latency_ms=10,
            tokens_used=10,
            cost_estimate_usd=0.01
        )

    async def health_check(self) -> bool:
        return True


class MockCacheManager(CacheManager):
    def __init__(self):
        self.hits = False
        self.stored = False

    async def lookup(self, prompt: str, category: str) -> CachedResponse | None:
        if self.hits:
            entry = CacheEntry(response="Cached response", created_at=123.0, provider="cache")
            return CachedResponse(entry, tier="exact")
        return None

    async def store(self, prompt: str, response: str, provider: str, category: str, ttl: int) -> None:
        self.stored = True


@pytest.fixture
def mock_config():
    return ModelStackConfig(
        DEEPSEEK_API_KEY=SecretStr("test"),
        MISTRAL_API_KEY=SecretStr("test"),
        local_enabled=False,
        router_escalation_confluence=140,
        router_escalation_on_anomaly=True
    )


@pytest.fixture
def router(mock_config):
    cache = MockCacheManager()
    ds_chat = MockLLMClient("deepseek-chat")
    ds_reasoner = MockLLMClient("deepseek-reasoner")
    local = MockLLMClient("local-model")
    return ComplexityRouter(mock_config, cache, ds_chat, ds_reasoner, local)


@pytest.mark.asyncio
async def test_routine_task_goes_to_chat(router):
    resp = await router.route_and_execute("Hello", "sentiment_classification")
    assert resp.model == "deepseek-chat"


@pytest.mark.asyncio
async def test_high_stakes_goes_to_reasoner(router):
    resp = await router.route_and_execute("Hello", "high_conviction_reasoning")
    assert resp.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_routine_with_anomalies_escalates_to_reasoner(router):
    resp = await router.route_and_execute("Hello", "sentiment_classification", has_anomalies=True)
    assert resp.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_conviction_score_escalates_to_reasoner(router):
    resp = await router.route_and_execute("Hello", "sentiment_classification", conviction_score=150)
    assert resp.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_local_used_when_enabled_for_routine(router, mock_config):
    mock_config.local_enabled = True
    resp = await router.route_and_execute("Hello", "sentiment_classification")
    assert resp.model == "local-model"


@pytest.mark.asyncio
async def test_cache_hit_bypasses_models(router):
    router._cache.hits = True
    resp = await router.route_and_execute("Hello", "sentiment_classification")
    assert resp.model == "cache"
    assert resp.text == "Cached response"
