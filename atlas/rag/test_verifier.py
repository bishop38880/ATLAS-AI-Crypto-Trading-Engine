import pytest
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock

from atlas.rag.verifier import (
    DocumentVerifier,
    VerificationStatus,
    VerificationFlag,
    ContradictionPair,
)

# Mock Document structure
class MockMemoryDocument:
    def __init__(self, **kwargs: Any) -> None:
        self.id = "doc-123"
        self.timestamp = datetime.now(timezone.utc)
        self.asset = "BTC"
        self.reasoning_summary = "Test reasoning"
        self.decision = "Buy"
        self.confluence_score = 150
        self.key_convergences = ["Bullish test convergence"]
        self.key_risks = ["Minor test risk"]
        for k, v in kwargs.items():
            setattr(self, k, v)


@pytest.fixture
def mock_qdrant() -> AsyncMock:
    client = AsyncMock()
    # By default, return no contradictions
    client.scroll.return_value = ([], None)
    return client


@pytest.fixture
def mock_redis() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def verifier(mock_qdrant: AsyncMock, mock_redis: AsyncMock) -> DocumentVerifier:
    return DocumentVerifier(
        qdrant_client=mock_qdrant,
        qdrant_collection="test_collection",
        redis_client=mock_redis,
    )


@pytest.mark.asyncio
async def test_timestamp_epoch(verifier: DocumentVerifier) -> None:
    # Test 1: Timestamp 1970-01-01 -> TIMESTAMP_ANOMALY
    doc = MockMemoryDocument(timestamp=datetime(1970, 1, 1, tzinfo=timezone.utc))
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.TIMESTAMP_ANOMALY
    assert any(f.rule_id == "RULE_TIMESTAMP_EPOCH" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_timestamp_future_5min(verifier: DocumentVerifier) -> None:
    # Test 2: Timestamp 6 minutes in future -> FLAGGED, RULE_TIMESTAMP_FUTURE
    doc = MockMemoryDocument(timestamp=datetime.now(timezone.utc) + timedelta(minutes=6))
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_TIMESTAMP_FUTURE" and f.severity == "warn" for f in report.flags)


@pytest.mark.asyncio
async def test_timestamp_future_1day(verifier: DocumentVerifier) -> None:
    # Test 3: Timestamp 1 day in future -> FLAGGED, RULE_TIMESTAMP_FUTURE
    doc = MockMemoryDocument(timestamp=datetime.now(timezone.utc) + timedelta(days=1))
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_TIMESTAMP_FUTURE" for f in report.flags)


@pytest.mark.asyncio
async def test_missing_fields(verifier: DocumentVerifier) -> None:
    # Test 4: Missing reasoning_summary -> FLAGGED, RULE_MISSING_FIELDS
    doc = MockMemoryDocument(reasoning_summary="")
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_MISSING_FIELDS" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_score_range(verifier: DocumentVerifier) -> None:
    # Test 5: Score 250 -> FLAGGED, RULE_SCORE_RANGE critical
    doc = MockMemoryDocument(confluence_score=250)
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_SCORE_RANGE" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_empty_key_lists(verifier: DocumentVerifier) -> None:
    # Test 6: Empty key_convergences AND key_risks -> FLAGGED, RULE_EMPTY_ANALYSIS warn
    doc = MockMemoryDocument(key_convergences=[], key_risks=[])
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_EMPTY_ANALYSIS" and f.severity == "warn" for f in report.flags)


@pytest.mark.asyncio
async def test_incoherent_direction(verifier: DocumentVerifier) -> None:
    # Test 7: REASONING clearly bullish (5 bullish keywords), CONVERGENCES clearly bearish (5 bearish keywords)
    reasoning = "bullish buy upward positive breakout"
    convergences = ["bearish sell downward negative breakdown"]
    doc = MockMemoryDocument(reasoning_summary=reasoning, key_convergences=convergences)
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.INCOHERENT
    assert any(f.rule_id == "RULE_INCOHERENT_DIRECTION" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_neutral_direction(verifier: DocumentVerifier) -> None:
    # Test 8: REASONING neutral (only 2 directional keywords) -> no coherence flag
    reasoning = "bullish buy" # Only 2, neutral
    convergences = ["bearish sell downward negative breakdown"] # Bearish
    doc = MockMemoryDocument(reasoning_summary=reasoning, key_convergences=convergences)
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.CLEAN # No critical flag, so clean
    assert not any(f.rule_id == "RULE_INCOHERENT_DIRECTION" for f in report.flags)


@pytest.mark.asyncio
async def test_decision_score_mismatch_buy(verifier: DocumentVerifier) -> None:
    # Test 9: Decision "Strong Buy" with score 95 -> FLAGGED, RULE_DECISION_SCORE_MISMATCH
    doc = MockMemoryDocument(decision="Strong Buy", confluence_score=95)
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_DECISION_SCORE_MISMATCH" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_decision_score_mismatch_hold(verifier: DocumentVerifier) -> None:
    # Test 10: Decision "Hold" with score 180 -> FLAGGED
    doc = MockMemoryDocument(decision="Hold", confluence_score=180)
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.FLAGGED
    assert any(f.rule_id == "RULE_DECISION_SCORE_MISMATCH" and f.severity == "critical" for f in report.flags)


@pytest.mark.asyncio
async def test_cross_direction_contradiction(verifier: DocumentVerifier, mock_qdrant: AsyncMock) -> None:
    # Test 11: Cross-document contradiction detected
    doc = MockMemoryDocument(decision="Buy", confluence_score=150)
    
    # Mock Qdrant to return an opposite-direction document > 140
    class MockPoint:
        def __init__(self) -> None:
            self.id = "doc-789"
            self.payload = {
                "decision": "Sell",
                "confluence_score": 150,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
    
    mock_qdrant.scroll.return_value = ([MockPoint()], None)
    
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.CONTRADICTED
    assert any(f.rule_id == "RULE_CROSS_DIRECTION_CONTRADICTION" for f in report.flags)
    assert len(report.contradictions) == 1
    assert report.contradictions[0].contradiction_type == "direction_flip"


@pytest.mark.asyncio
async def test_clean_document(verifier: DocumentVerifier) -> None:
    # Test 12: Clean document
    doc = MockMemoryDocument()
    report = await verifier.verify(doc)
    assert report.status == VerificationStatus.CLEAN
    assert len(report.flags) == 0
    assert report.contradiction_note == ""
