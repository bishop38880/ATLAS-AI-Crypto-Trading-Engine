"""Tests for RAG Query Engine — freshness decay, depth params, LanceDB conversion.

Covers:
    - Freshness decay re-ranking with identical similarity scores
    - Freshness disabled passthrough
    - Clock skew / future document handling
    - LanceDB distance-to-similarity conversion
    - Depth-to-params mapping (shallow, deep)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import msgspec
import pytest
from qdrant_client.models import FieldCondition, Filter, MatchValue

from atlas.rag.query import (
    RAGQueryEngine,
    RetrievalDepth,
    ScoredDocument,
    SIGNAL_MEMORY_COLLECTION,
    _merge_archived_filter,
)
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def settings() -> PolarisSettings:
    """Default PolarisSettings with freshness enabled."""
    return PolarisSettings(_env_file=None)


@pytest.fixture()
def engine(settings: PolarisSettings) -> RAGQueryEngine:
    """RAGQueryEngine with mock backends (only testing pure methods)."""
    return RAGQueryEngine(
        settings=settings,
        qdrant_client=MagicMock(),
        lancedb_conn=MagicMock(),
        embedding_service=MagicMock(),
    )


def _make_doc(
    doc_id: str,
    similarity: float,
    age_hours: float,
    now: datetime,
) -> ScoredDocument:
    """Create a ScoredDocument with a given age relative to now."""
    ts = now - timedelta(hours=age_hours)
    return ScoredDocument(
        document_id=doc_id,
        similarity_score=similarity,
        timestamp=ts,
    )


# ---------------------------------------------------------------------------
# test_freshness_decay_reranks_old_docs_below_new
# ---------------------------------------------------------------------------


def test_freshness_decay_reranks_old_docs_below_new(
    engine: RAGQueryEngine,
) -> None:
    """Two docs with identical similarity — 2h-old wins over 48h-old."""
    now = datetime.now(timezone.utc)
    doc_new = _make_doc("new", similarity=0.9, age_hours=2.0, now=now)
    doc_old = _make_doc("old", similarity=0.9, age_hours=48.0, now=now)

    result = engine._apply_freshness_decay([doc_old, doc_new], now)

    assert result[0].document_id == "new"
    assert result[1].document_id == "old"
    assert result[0].final_score > result[1].final_score

    # Verify math: 2h decay weight
    lam = 0.05
    expected_new_weight = math.exp(-lam * 2.0)
    expected_old_weight = math.exp(-lam * 48.0)
    assert abs(result[0].final_score - 0.9 * expected_new_weight) < 1e-9
    assert abs(result[1].final_score - 0.9 * expected_old_weight) < 1e-9


# ---------------------------------------------------------------------------
# test_freshness_disabled_returns_unchanged_order
# ---------------------------------------------------------------------------


def test_freshness_disabled_returns_unchanged_order() -> None:
    """When freshness is disabled, final_score = similarity_score."""
    settings = PolarisSettings(rag_freshness_enabled=False)
    engine = RAGQueryEngine(
        settings=settings,
        qdrant_client=MagicMock(),
        lancedb_conn=MagicMock(),
        embedding_service=MagicMock(),
    )
    now = datetime.now(timezone.utc)
    doc_a = _make_doc("a", similarity=0.7, age_hours=100.0, now=now)
    doc_b = _make_doc("b", similarity=0.8, age_hours=1.0, now=now)

    # Input order: [a, b] — a has lower similarity but is first
    result = engine._apply_freshness_decay([doc_a, doc_b], now)

    # Should NOT sort — returns in input order
    assert result[0].document_id == "a"
    assert result[1].document_id == "b"
    assert result[0].final_score == 0.7
    assert result[1].final_score == 0.8


# ---------------------------------------------------------------------------
# test_clock_skew_future_doc_weight_is_1_0
# ---------------------------------------------------------------------------


def test_clock_skew_future_doc_weight_is_1_0(
    engine: RAGQueryEngine,
) -> None:
    """A document with timestamp in the future gets weight 1.0 (no decay)."""
    now = datetime.now(timezone.utc)
    future_doc = ScoredDocument(
        document_id="future",
        similarity_score=0.85,
        timestamp=now + timedelta(hours=5),
    )

    result = engine._apply_freshness_decay([future_doc], now)

    # age clamped to 0 → weight = exp(0) = 1.0
    assert abs(result[0].final_score - 0.85) < 1e-9


# ---------------------------------------------------------------------------
# test_lance_distance_converted_to_similarity_before_decay
# ---------------------------------------------------------------------------


def test_lance_distance_converted_to_similarity_before_decay(
    engine: RAGQueryEngine,
) -> None:
    """LanceDB cosine distance [0, 2] converts correctly to similarity [0, 1]."""
    # distance=0 → identical → similarity=1.0
    assert abs(engine._lancedb_distance_to_similarity(0.0) - 1.0) < 1e-9

    # distance=2 → opposite → similarity=0.0
    assert abs(engine._lancedb_distance_to_similarity(2.0) - 0.0) < 1e-9

    # distance=1 → orthogonal → similarity=0.5
    assert abs(engine._lancedb_distance_to_similarity(1.0) - 0.5) < 1e-9

    # distance=0.4 → similarity=0.8
    assert abs(engine._lancedb_distance_to_similarity(0.4) - 0.8) < 1e-9

    # Negative distance (shouldn't happen) → clamped to max 1.0
    result = engine._lancedb_distance_to_similarity(-0.5)
    assert result <= 1.0
    assert result >= 0.0


# ---------------------------------------------------------------------------
# test_depth_shallow_uses_top_k_3_ef_50
# ---------------------------------------------------------------------------


def test_depth_shallow_uses_top_k_3_ef_50(
    engine: RAGQueryEngine,
) -> None:
    """SHALLOW depth maps to top_k=3, hnsw_ef=50."""
    top_k, ef = engine._depth_to_params(RetrievalDepth.SHALLOW)
    assert top_k == 3
    assert ef == 50


# ---------------------------------------------------------------------------
# test_depth_deep_uses_top_k_15_ef_150
# ---------------------------------------------------------------------------


def test_depth_deep_uses_top_k_15_ef_150(
    engine: RAGQueryEngine,
) -> None:
    """DEEP depth maps to top_k=15, hnsw_ef=150."""
    top_k, ef = engine._depth_to_params(RetrievalDepth.DEEP)
    assert top_k == 15
    assert ef == 150


# ---------------------------------------------------------------------------
# test_depth_default_uses_top_k_10_ef_100
# ---------------------------------------------------------------------------


def test_depth_default_uses_top_k_10_ef_100(
    engine: RAGQueryEngine,
) -> None:
    """DEFAULT depth maps to top_k=10, hnsw_ef=100."""
    top_k, ef = engine._depth_to_params(RetrievalDepth.DEFAULT)
    assert top_k == 10
    assert ef == 100


# ---------------------------------------------------------------------------
# test_one_week_clamp_prevents_underflow
# ---------------------------------------------------------------------------


def test_one_week_clamp_prevents_underflow(
    engine: RAGQueryEngine,
) -> None:
    """Documents older than 1 week are clamped at the 1-week weight."""
    now = datetime.now(timezone.utc)
    doc_very_old = _make_doc("ancient", similarity=0.9, age_hours=500.0, now=now)
    doc_one_week = _make_doc("week", similarity=0.9, age_hours=168.0, now=now)

    result = engine._apply_freshness_decay([doc_very_old, doc_one_week], now)

    # Both should have the same final_score (clamped at 168h)
    assert abs(result[0].final_score - result[1].final_score) < 1e-9


# ---------------------------------------------------------------------------
# test_scored_document_is_frozen
# ---------------------------------------------------------------------------


def test_scored_document_is_frozen() -> None:
    """ScoredDocument is a frozen Pydantic model — mutation must raise."""
    doc = ScoredDocument(
        document_id="test",
        similarity_score=0.5,
        timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises(Exception):
        doc.similarity_score = 0.9  # type: ignore[misc]


def test_query_engine_defaults_to_signal_memory_collection(
    engine: RAGQueryEngine,
) -> None:
    """Read-side collection must match RAGPipeline's signal-memory writes."""
    assert engine._collection == SIGNAL_MEMORY_COLLECTION


def test_archived_filter_excludes_true_without_requiring_false_field() -> None:
    """Documents missing archived should remain eligible for retrieval."""
    result = _merge_archived_filter(existing=None, include_archived=False)

    assert result is not None
    assert result.must is None
    assert result.must_not is not None
    assert len(result.must_not) == 1


def test_archived_filter_preserves_asset_filter() -> None:
    existing = Filter(
        must=[
            FieldCondition(
                key="asset",
                match=MatchValue(value="BTC/USDT"),
            ),
        ],
    )

    result = _merge_archived_filter(existing=existing, include_archived=False)

    assert result is not None
    assert result.must == existing.must
    assert result.must_not is not None
    assert len(result.must_not) == 1


def test_lancedb_metadata_payload_and_cycle_timestamp_are_parsed(
    engine: RAGQueryEngine,
) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "document_id": "doc-1",
        "asset": "BTC/USDT",
        "cycle_timestamp": now.isoformat(),
        "signal_score": 62,
    }
    rows = [
        {
            "id": "doc-1",
            "_distance": 0.4,
            "metadata": msgspec.json.encode(payload).decode(),
            "vector": [0.0] * 3,
        },
    ]

    result = engine._parse_lance_results(rows)

    assert result[0].document_id == "doc-1"
    assert result[0].similarity_score == 0.8
    assert result[0].payload["asset"] == "BTC/USDT"
    assert result[0].timestamp == now


# ---------------------------------------------------------------------------
# test_qdrant_failure_returns_empty (degraded mode)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qdrant_failure_returns_empty() -> None:
    """When Qdrant search raises, _search_qdrant returns [] (degraded)."""
    settings = PolarisSettings()
    mock_qdrant = MagicMock()
    mock_qdrant.search = MagicMock(side_effect=ConnectionError("Qdrant down"))
    engine = RAGQueryEngine(
        settings=settings,
        qdrant_client=mock_qdrant,
        lancedb_conn=MagicMock(),
        embedding_service=MagicMock(),
    )

    result = await engine._search_qdrant(
        embedding=[0.0] * 1024, limit=10, ef=100, qdrant_filter=None,
    )

    assert result == []


# ---------------------------------------------------------------------------
# test_decay_returns_new_instances_not_mutated_originals
# ---------------------------------------------------------------------------


def test_decay_returns_new_instances_not_mutated_originals(
    engine: RAGQueryEngine,
) -> None:
    """Freshness decay produces new ScoredDocument copies, not mutations."""
    now = datetime.now(timezone.utc)
    original = _make_doc("orig", similarity=0.9, age_hours=5.0, now=now)
    original_final = original.final_score  # should be 0.0

    result = engine._apply_freshness_decay([original], now)

    # The returned doc has final_score set
    assert result[0].final_score > 0.0
    # The original object was NOT mutated (frozen model + model_copy)
    assert original.final_score == original_final

