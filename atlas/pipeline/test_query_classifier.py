"""Tests for S3-P6 Query Classifier — all 7 spec tests + frozen check.

All tests mock redis.asyncio.Redis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import msgspec
import pytest

from atlas.pipeline.query_classifier import (
    QueryClassification,
    QueryClassifier,
    _CachePayload,
)


@pytest.fixture()
def mock_redis() -> AsyncMock:
    """Provide a mock redis.asyncio.Redis client."""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    return mock


@pytest.fixture()
def classifier(mock_redis: AsyncMock) -> QueryClassifier:
    """QueryClassifier wired to mock Redis."""
    return QueryClassifier(redis_client=mock_redis)


# ── Test 1: DIRECT ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_explain_funding_rate_routes_direct(
    classifier: QueryClassifier,
) -> None:
    """'explain what funding rate means' → DIRECT, confidence ≥ 0.95."""
    result = await classifier.classify("explain what funding rate means")
    assert result.route == "DIRECT"
    assert result.confidence >= 0.95


# ── Test 2: MCP_ONLY (DIRECT skipped — ETH in MONITORED_ASSETS) ──


@pytest.mark.asyncio
async def test_current_funding_rate_eth_routes_mcp(
    classifier: QueryClassifier,
) -> None:
    """'what is the current funding rate for ETH' → MCP_ONLY.

    DIRECT is skipped because ETH is in MONITORED_ASSETS.
    """
    result = await classifier.classify(
        "what is the current funding rate for ETH",
    )
    assert result.route == "MCP_ONLY"


# ── Test 3: RAG_ONLY ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_last_time_btc_routes_rag_only(
    classifier: QueryClassifier,
) -> None:
    """'last time BTC had this setup' → RAG_ONLY."""
    result = await classifier.classify("last time BTC had this setup")
    assert result.route == "RAG_ONLY"


# ── Test 4: HYBRID (explicit phrase) ─────────────────────────


@pytest.mark.asyncio
async def test_compare_current_to_historical_routes_hybrid(
    classifier: QueryClassifier,
) -> None:
    """'compare current sentiment to historical bull runs' → HYBRID."""
    result = await classifier.classify(
        "compare current sentiment to historical bull runs",
    )
    assert result.route == "HYBRID"


# ── Test 5: Fallback ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_garbled_phrase_routes_hybrid_fallback(
    classifier: QueryClassifier,
) -> None:
    """Garbled phrase with no keywords → HYBRID, fallback_to_hybrid=True."""
    result = await classifier.classify(
        "xyzzyx blorf quuxbar nothing meaningful",
    )
    assert result.route == "HYBRID"
    assert result.fallback_to_hybrid is True
    assert result.confidence == 0.60


# ── Test 6: Cache hit ────────────────────────────────────────


@pytest.mark.asyncio
async def test_second_query_is_cache_hit(
    classifier: QueryClassifier,
    mock_redis: AsyncMock,
) -> None:
    """Same query twice → second has cache_hit=True; classify fn called once."""
    query = "explain what funding rate means"

    first = await classifier.classify(query)
    assert first.cache_hit is False

    # Simulate Redis returning the cached payload on second call.
    cached_payload = _CachePayload(
        route=first.route,
        confidence=first.confidence,
        detected_signals=first.detected_signals,
        fallback_to_hybrid=first.fallback_to_hybrid,
        query_hash=first.query_hash,
        retrieval_depth=getattr(first, "retrieval_depth", "standard"),
    )
    mock_redis.get = AsyncMock(
        return_value=msgspec.json.encode(cached_payload),
    )

    second = await classifier.classify(query)
    assert second.cache_hit is True
    assert second.route == first.route


# ── Test 7: HYBRID from REALTIME + HISTORICAL (Step B) ───────


@pytest.mark.asyncio
async def test_realtime_plus_historical_routes_hybrid(
    classifier: QueryClassifier,
) -> None:
    """'what is the current BTC price vs historical average' → HYBRID.

    Step B catches both REALTIME and HISTORICAL keywords first.
    """
    result = await classifier.classify(
        "what is the current BTC price vs historical average",
    )
    assert result.route == "HYBRID"
    assert result.confidence == 0.85


# ── Frozen model invariant ───────────────────────────────────


def test_query_classification_model_is_frozen() -> None:
    """QueryClassification must be immutable (frozen=True)."""
    model = QueryClassification(
        route="DIRECT",
        confidence=0.9,
        detected_signals=["direct"],
        fallback_to_hybrid=False,
        cache_hit=False,
        query_hash="abcd1234abcd1234",
    )
    with pytest.raises(Exception):
        model.route = "HYBRID"  # type: ignore[misc]
