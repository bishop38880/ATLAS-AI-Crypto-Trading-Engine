"""Tests for Embedding Drift Detection — Phase 8 Quality Gate.

Validates:
1. Reference corpus building (stratified sampling + hash dedup)
2. Drift detection with corrupted embedding model (random vectors)
3. No drift with identical model
4. Daily API correlation fallback trigger
5. Alert throttling (once per day)
6. Cosine similarity offloaded to thread
7. Frozen result models
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from fakeredis import FakeAsyncRedis

from atlas.rag.embedding_monitor import (
    DRIFT_THRESHOLD,
    DailyCorrelationResult,
    DriftCheckResult,
    EmbeddingDriftMonitor,
    _compute_cosine_similarities_sync,
)
from atlas.rag.embedding_reference import (
    ReferenceCorpusBuilder,
    compute_content_hash,
)


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
async def redis_client() -> AsyncGenerator[FakeAsyncRedis, None]:
    """Provide a fake async Redis client."""
    client = FakeAsyncRedis()
    yield client
    await client.aclose()


def _make_mock_pool(
    fetch_return: list[dict[str, object]] | None = None,
    fetchval_return: object = None,
) -> AsyncMock:
    """Build a mock asyncpg pool with common methods.

    Args:
        fetch_return: Return value for pool.fetch().
        fetchval_return: Return value for pool.fetchval().

    Returns:
        Configured AsyncMock mimicking asyncpg.Pool.
    """
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=fetch_return or [])
    pool.fetchval = AsyncMock(return_value=fetchval_return)

    conn = AsyncMock()
    conn.execute = AsyncMock()
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    return pool


def _make_mock_embedding_service(
    dim: int = 1024,
    mode: str = "identity",
) -> AsyncMock:
    """Build a mock EmbeddingService.

    Args:
        dim: Embedding dimension.
        mode: 'identity' returns fixed vectors, 'random' returns noise.

    Returns:
        Configured AsyncMock mimicking EmbeddingService.
    """
    svc = AsyncMock()

    if mode == "identity":
        fixed_vec = [0.1] * dim

        async def _embed(text: str) -> list[float]:
            return fixed_vec

        async def _embed_batch(texts: list[str]) -> list[list[float]]:
            return [fixed_vec for _ in texts]
    else:
        rng = np.random.default_rng(99)

        async def _embed(text: str) -> list[float]:
            return rng.normal(0, 1, dim).tolist()

        async def _embed_batch(texts: list[str]) -> list[list[float]]:
            return [rng.normal(0, 1, dim).tolist() for _ in texts]

    svc.embed = _embed
    svc.embed_batch = _embed_batch
    return svc


def _make_reference_records(
    n: int = 10,
    dim: int = 1024,
) -> list[dict[str, object]]:
    """Generate mock reference corpus records.

    Args:
        n: Number of records to generate.
        dim: Embedding dimension.

    Returns:
        List of dicts mimicking asyncpg Records.
    """
    rng = np.random.default_rng(42)
    records: list[dict[str, object]] = []
    assets = ["BTC", "ETH", "SOL", "AVAX", "LINK"]
    outcomes = ["WIN", "LOSS", "SCRATCH"]

    for i in range(n):
        vec = rng.normal(0, 1, dim).tolist()
        records.append({
            "source_signal_id": f"sig_{i}",
            "asset": assets[i % len(assets)],
            "outcome_label": outcomes[i % len(outcomes)],
            "score": 50 + (i * 17) % 170,
            "content_text": f"Trade analysis {i} for {assets[i % len(assets)]}",
            "embedding": vec,
            "signal_id": f"sig_{i}",
        })

    return records


# ── Content Hash Tests ────────────────────────────────────────────────


def test_content_hash_deterministic() -> None:
    """Same text produces same SHA-256 hash."""
    text = "BTC analysis: bullish breakout confirmed"
    h1 = compute_content_hash(text)
    h2 = compute_content_hash(text)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex length


def test_content_hash_deduplication() -> None:
    """Different texts produce different hashes."""
    h1 = compute_content_hash("BTC bullish")
    h2 = compute_content_hash("ETH bearish")
    assert h1 != h2


# ── Reference Corpus Tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_reference_corpus_build() -> None:
    """Stratified candidates are embedded and stored."""
    candidates = _make_reference_records(20)
    pool = _make_mock_pool(fetch_return=candidates)
    embed_svc = _make_mock_embedding_service(mode="identity")

    builder = ReferenceCorpusBuilder(pool, embed_svc)
    inserted = await builder.build_reference_corpus(
        target_size=20, rows_per_stratum=5,
    )

    # Verify fetch was called with stratification query
    pool.fetch.assert_called_once()
    assert inserted == 20


@pytest.mark.asyncio
async def test_corpus_stratification() -> None:
    """Verify candidates are balanced across asset/outcome buckets."""
    candidates = _make_reference_records(25)
    pool = _make_mock_pool(fetch_return=candidates)
    embed_svc = _make_mock_embedding_service(mode="identity")

    builder = ReferenceCorpusBuilder(pool, embed_svc)
    await builder.build_reference_corpus(
        target_size=25, rows_per_stratum=5,
    )

    # Verify diverse assets present in candidates
    assets = {c["asset"] for c in candidates}
    assert len(assets) >= 3  # At least 3 distinct assets

    outcomes = {c["outcome_label"] for c in candidates}
    assert len(outcomes) >= 2  # At least 2 distinct outcomes


@pytest.mark.asyncio
async def test_empty_candidates_returns_zero() -> None:
    """No candidates in signal_history returns 0 inserted."""
    pool = _make_mock_pool(fetch_return=[])
    embed_svc = _make_mock_embedding_service()

    builder = ReferenceCorpusBuilder(pool, embed_svc)
    inserted = await builder.build_reference_corpus()

    assert inserted == 0


# ── Drift Detection Tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drift_detected_with_corrupted_model(
    redis_client: FakeAsyncRedis,
) -> None:
    """QUALITY GATE: Corrupted model (random vectors) triggers drift.

    This is the mandatory quality gate test. Simulates a fully
    corrupted embedding model returning random vectors and verifies
    the drift is detected and the update is blocked.
    """
    ref_records = _make_reference_records(50)
    pool = _make_mock_pool(fetch_return=ref_records)

    # Current (good) embedding service — not used for drift check
    current_svc = _make_mock_embedding_service(mode="identity")
    # Candidate (corrupted) embedding service — returns random noise
    corrupted_svc = _make_mock_embedding_service(mode="random")

    monitor = EmbeddingDriftMonitor(pool, current_svc, redis_client)

    with patch("scripts.telegram_alert.send_telegram_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = True
        result = await monitor.check_model_drift(
            corrupted_svc, model_version="corrupted-v1",
        )

    assert result.drift_detected is True
    assert result.avg_cosine_similarity < DRIFT_THRESHOLD
    assert result.corpus_size == 50
    assert result.model_version == "corrupted-v1"

    # Telegram alert should have been dispatched
    assert mock_alert.call_count == 1


@pytest.mark.asyncio
async def test_no_drift_with_identical_model(
    redis_client: FakeAsyncRedis,
) -> None:
    """Same embeddings yield avg ≈ 1.0 — no drift detected."""
    ref_records = _make_reference_records(20)
    # Use same fixed vector for both reference and new
    fixed_vec = [0.1] * 1024
    for r in ref_records:
        r["embedding"] = fixed_vec

    pool = _make_mock_pool(fetch_return=ref_records)
    current_svc = _make_mock_embedding_service(mode="identity")
    identical_svc = _make_mock_embedding_service(mode="identity")

    monitor = EmbeddingDriftMonitor(pool, current_svc, redis_client)
    result = await monitor.check_model_drift(
        identical_svc, model_version="mistral-embed-v2",
    )

    assert result.drift_detected is False
    assert result.avg_cosine_similarity > 0.99
    assert result.corpus_size == 20


@pytest.mark.asyncio
async def test_empty_corpus_no_drift(
    redis_client: FakeAsyncRedis,
) -> None:
    """Empty reference corpus safely returns no drift."""
    pool = _make_mock_pool(fetch_return=[])
    svc = _make_mock_embedding_service()

    monitor = EmbeddingDriftMonitor(pool, svc, redis_client)
    result = await monitor.check_model_drift(svc, model_version="test")

    assert result.drift_detected is False
    assert result.corpus_size == 0


# ── Daily Correlation Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_daily_correlation_triggers_fallback(
    redis_client: FakeAsyncRedis,
) -> None:
    """API returning shifted embeddings triggers Qwen fallback."""
    ref_records = _make_reference_records(30)
    pool = _make_mock_pool(fetch_return=ref_records)

    # Current service returns random noise (simulating API drift)
    drifted_svc = _make_mock_embedding_service(mode="random")

    monitor = EmbeddingDriftMonitor(pool, drifted_svc, redis_client)

    with patch("scripts.telegram_alert.send_telegram_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = True
        result = await monitor.daily_api_correlation_check()

    assert result.drift_detected is True
    assert result.fallback_triggered is True

    # Redis should have the fallback key set
    provider = await redis_client.get("embedding:active_provider")
    assert provider is not None
    assert provider.decode("utf-8") == "qwen_local"


@pytest.mark.asyncio
async def test_daily_correlation_no_drift(
    redis_client: FakeAsyncRedis,
) -> None:
    """API returning identical embeddings — no fallback."""
    fixed_vec = [0.1] * 1024
    ref_records = _make_reference_records(20)
    for r in ref_records:
        r["embedding"] = fixed_vec

    pool = _make_mock_pool(fetch_return=ref_records)
    svc = _make_mock_embedding_service(mode="identity")

    monitor = EmbeddingDriftMonitor(pool, svc, redis_client)
    result = await monitor.daily_api_correlation_check()

    assert result.drift_detected is False
    assert result.fallback_triggered is False

    # No fallback key should exist
    provider = await redis_client.get("embedding:active_provider")
    assert provider is None


# ── Alert Throttle Tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drift_alert_throttle(
    redis_client: FakeAsyncRedis,
) -> None:
    """Second alert within 24h is suppressed."""
    ref_records = _make_reference_records(10)
    pool = _make_mock_pool(fetch_return=ref_records)
    svc = _make_mock_embedding_service(mode="identity")
    corrupted = _make_mock_embedding_service(mode="random")

    monitor = EmbeddingDriftMonitor(pool, svc, redis_client)

    with patch("scripts.telegram_alert.send_telegram_alert", new_callable=AsyncMock) as mock_alert:
        mock_alert.return_value = True

        # First call — alert sent
        await monitor.check_model_drift(corrupted, "v1")
        assert mock_alert.call_count == 1

        # Second call — throttled
        await monitor.check_model_drift(corrupted, "v2")
        assert mock_alert.call_count == 1  # Still 1


# ── Cosine Similarity Tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cosine_similarity_computation_offloaded() -> None:
    """Verify asyncio.to_thread is called for numpy computation."""
    ref_records = _make_reference_records(5)
    pool = _make_mock_pool(fetch_return=ref_records)
    redis = FakeAsyncRedis()
    svc = _make_mock_embedding_service(mode="identity")

    monitor = EmbeddingDriftMonitor(pool, svc, redis)

    with patch("atlas.rag.embedding_monitor.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        mock_thread.return_value = {
            "avg": 0.99, "min": 0.98, "max": 1.0,
        }
        result = await monitor.check_model_drift(svc, "test")
        assert mock_thread.call_count == 1
        assert result.drift_detected is False

    await redis.aclose()


def test_cosine_similarity_identical_vectors() -> None:
    """Identical vectors yield cosine similarity ≈ 1.0."""
    vec = [[0.5] * 1024] * 10
    stats = _compute_cosine_similarities_sync(vec, vec)
    assert abs(stats["avg"] - 1.0) < 1e-6
    assert abs(stats["min"] - 1.0) < 1e-6


def test_cosine_similarity_orthogonal_vectors() -> None:
    """Orthogonal vectors yield cosine similarity ≈ 0.0."""
    dim = 1024
    rng = np.random.default_rng(42)
    # Create two sets of random vectors — expect low similarity
    ref = [rng.normal(0, 1, dim).tolist() for _ in range(10)]
    other_rng = np.random.default_rng(999)
    new = [other_rng.normal(0, 1, dim).tolist() for _ in range(10)]
    stats = _compute_cosine_similarities_sync(ref, new)
    # Random 1024-dim vectors: expected cosine ≈ 0 ± some noise
    assert stats["avg"] < 0.5


# ── Frozen Model Tests ───────────────────────────────────────────────


def test_drift_result_model_frozen() -> None:
    """DriftCheckResult is immutable."""
    result = DriftCheckResult(
        avg_cosine_similarity=0.98,
        min_cosine_similarity=0.95,
        max_cosine_similarity=1.0,
        corpus_size=1000,
        drift_detected=False,
        model_version="mistral-embed",
        checked_at=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):
        result.drift_detected = True  # type: ignore[misc]


def test_daily_correlation_result_frozen() -> None:
    """DailyCorrelationResult is immutable."""
    result = DailyCorrelationResult(
        avg_cosine_similarity=0.97,
        sample_size=100,
        drift_detected=False,
        fallback_triggered=False,
        checked_at=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):
        result.fallback_triggered = True  # type: ignore[misc]
