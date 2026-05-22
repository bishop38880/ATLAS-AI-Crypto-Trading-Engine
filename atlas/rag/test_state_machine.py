"""Tests for the RAG Document Verification State Machine."""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch, MagicMock

from atlas.rag.state_machine import (
    DocumentState,
    DocumentStateMachine,
    ContradictionAccumulation,
)
from atlas.rag.agent_zero.state_manager import AgentZeroStateManager
from atlas.rag.verifier import VerificationStatus, VerifierReport, DocumentVerifier
from atlas.shared.config import PolarisSettings

pytestmark = pytest.mark.asyncio

@pytest.fixture
def mock_pool():
    class AsyncContextManagerMock:
        def __init__(self, obj: Any):
            self.obj = obj
        async def __aenter__(self):
            return self.obj
        async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any):
            pass

    pool = MagicMock()
    conn = MagicMock()
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.fetch = AsyncMock()
    conn.execute = AsyncMock()
    
    pool.acquire.return_value = AsyncContextManagerMock(conn)
    conn.transaction.return_value = AsyncContextManagerMock(conn)
    
    return pool

@pytest.fixture
def mock_redis():
    return AsyncMock()

@pytest.fixture
def mock_qdrant():
    return AsyncMock()

@pytest.fixture
def state_machine(mock_pool, mock_redis):
    return DocumentStateMachine(mock_pool, mock_redis)

@pytest.fixture
def verifier():
    return AsyncMock()

@pytest.fixture
def state_manager(mock_pool, state_machine, verifier, mock_qdrant):
    return AgentZeroStateManager(mock_pool, state_machine, verifier, mock_qdrant, "test_collection")

async def test_valid_transition_flow(state_machine: DocumentStateMachine, mock_pool: AsyncMock, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"raw"
    conn = mock_pool.acquire.return_value.obj
    conn.fetchrow.return_value = {"escore": Decimal("1.0"), "contradiction_count": 0}
    
    await state_machine.transition("doc1", "BTC", DocumentState.PENDING_VERIFICATION, "testing", "test")
    assert conn.execute.call_count == 2

# 2. test_invalid_transition_verified_to_raw_raises
async def test_invalid_transition_verified_to_raw_raises(state_machine: DocumentStateMachine, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"verified"
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition("doc1", "BTC", DocumentState.RAW, "testing", "test")

# 3. test_invalid_transition_soft_deleted_to_verified_raises
async def test_invalid_transition_soft_deleted_to_verified_raises(state_machine: DocumentStateMachine, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"soft_deleted"
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition("doc1", "BTC", DocumentState.VERIFIED, "testing", "test")

# 4. test_invalid_transition_from_archived_raises
async def test_invalid_transition_from_archived_raises(state_machine: DocumentStateMachine, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"archived"
    with pytest.raises(ValueError, match="Invalid transition"):
        await state_machine.transition("doc1", "BTC", DocumentState.SOFT_DELETED, "testing", "test")

# 5. test_contradiction_threshold_triggers_reverification
async def test_contradiction_threshold_triggers_reverification(state_machine: DocumentStateMachine, mock_pool: MagicMock, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"verified"
    conn = mock_pool.acquire.return_value.obj
    conn.fetchrow.return_value = {"escore": Decimal("1.0"), "contradiction_count": 3}
    conn.fetchval.return_value = 3 # new count
    
    accum = ContradictionAccumulation("doc1", "doc2", "BTC", "Buy", "Sell", 150, 150, Decimal("1.0"))
    await state_machine.record_contradiction(accum)
    
    # Assert that transition was triggered, causing an UPDATE
    executed_queries = [call[0][0] for call in conn.execute.call_args_list]
    assert any("UPDATE signal_history" in query for query in executed_queries)

# 6. test_flagged_review_auto_expires
async def test_flagged_review_auto_expires(state_manager: AgentZeroStateManager, mock_pool: MagicMock, mock_redis: AsyncMock) -> None:
    mock_redis.get.return_value = b"flagged_review"
    conn = mock_pool.acquire.return_value.obj
    conn.fetch.return_value = [{"signal_id": "doc1"}]
    conn.fetchval.return_value = "BTC"
    conn.fetchrow.return_value = {"escore": Decimal("1.0"), "contradiction_count": 0}

    s, e = await state_manager._expire_flagged_documents()
    assert s == 1
    assert e == 0
    executed_queries = [call[0][0] for call in conn.execute.call_args_list]
    assert any("UPDATE signal_history" in query for query in executed_queries)

# 7. test_soft_deleted_aging
async def test_soft_deleted_aging(state_manager: AgentZeroStateManager, mock_pool: MagicMock, mock_redis: AsyncMock, mock_qdrant: AsyncMock) -> None:
    mock_redis.get.return_value = b"soft_deleted"
    conn = mock_pool.acquire.return_value.obj
    conn.fetch.return_value = [{"signal_id": "doc1"}]
    conn.fetchval.return_value = "BTC"
    conn.fetchrow.return_value = {"escore": Decimal("1.0"), "contradiction_count": 0}

    a, e = await state_manager._age_soft_deleted_documents()
    assert a == 1
    assert e == 0
    assert mock_qdrant.set_payload.call_count == 1

# 8. test_contradiction_recorded_against_opposite_direction
async def test_contradiction_recorded_against_opposite_direction() -> None:
    with patch("atlas.shared.config.PolarisSettings") as mock_settings:
        mock_settings.return_value.recursive_verify_enabled = True
        
        v = DocumentVerifier(AsyncMock(), "col", AsyncMock())
        class MockDoc:
            id = "doc2"
            decision = "Sell"
            asset = "BTC"
            timestamp = datetime.now(timezone.utc)
            confluence_score = 160
        
        report = VerifierReport(
            document_id="doc2", status=VerificationStatus.CLEAN,
            flags=(), contradictions=(), verified_at=datetime.now(timezone.utc),
            duration_ms=0, contradiction_note=""
        )
        sm = AsyncMock()
        
        class MockPoint:
            id = "doc1"
            payload = {
                "decision": "Buy",
                "confluence_score": 150,
                "timestamp": "2023-01-01T00:00:00+00:00"
            }
        
        with patch.object(v, "_scan_for_opposite_signals", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = [MockPoint()]
            await v.check_and_record_contradictions(MockDoc(), report, sm)
            
        assert sm.record_contradiction.call_count == 1

# 9. test_low_score_document_no_propagation
async def test_low_score_document_no_propagation() -> None:
    with patch("atlas.shared.config.PolarisSettings") as mock_settings:
        mock_settings.return_value.recursive_verify_enabled = True
        
        v = DocumentVerifier(AsyncMock(), "col", AsyncMock())
        class MockDoc:
            id = "doc2"
            decision = "Sell"
            asset = "BTC"
            timestamp = datetime.now(timezone.utc)
            confluence_score = 160
        
        report = VerifierReport(
            document_id="doc2", status=VerificationStatus.CLEAN,
            flags=(), contradictions=(), verified_at=datetime.now(timezone.utc),
            duration_ms=0, contradiction_note=""
        )
        sm = AsyncMock()
        
        class MockPointLowScore:
            id = "doc1"
            payload = {
                "decision": "Buy",
                "confluence_score": 100, 
                "timestamp": "2023-01-01T00:00:00+00:00"
            }
        
        with patch.object(v, "_scan_for_opposite_signals", new_callable=AsyncMock) as mock_scan:
            mock_scan.return_value = [] # Low score is filtered out before it gets to process
            await v.check_and_record_contradictions(MockDoc(), report, sm)
            
        assert sm.record_contradiction.call_count == 0

# 10. test_contradiction_penalty_reduces_escore
async def test_contradiction_penalty_reduces_escore(state_manager: AgentZeroStateManager, mock_pool: MagicMock, mock_redis: AsyncMock, verifier: AsyncMock) -> None:
    mock_redis.get.return_value = b"pending_verification"
    conn = mock_pool.acquire.return_value.obj
    
    conn.fetch.return_value = [{"signal_id": "doc1"}] # get_documents_pending_verification
    conn.fetchrow.return_value = {
        "signal_id": "doc1", "decision": "Buy", "asset": "BTC", 
        "created_at": datetime.now(), "raw_score": 150, "reasoning": "...", "metadata": {}
    }
    
    # Fetchval is used multiple times, need to side_effect it
    # 1. ccount = await conn.fetchval(...) -> returns 2
    # 2. _sm.transition checks asset -> gets "BTC"
    async def fetchval_se(*args: tuple[Any, ...], **kwargs: dict[str, Any]) -> Any:
        if "contradiction_count" in args[0]:
            return 2 # 2 * 0.05 = 0.10 penalty
        if "SELECT asset FROM signal_history" in args[0]:
            return "BTC"
        if "SELECT document_state" in args[0]:
            return "pending_verification"
        return None
    
    conn.fetchval.side_effect = fetchval_se
    
    verifier.verify.return_value = VerifierReport(
        document_id="doc1", status=VerificationStatus.CLEAN,
        flags=(), contradictions=(), verified_at=datetime.now(timezone.utc),
        duration_ms=0, contradiction_note=""
    )
    
    await state_manager._process_pending_queue()
    
    executed_queries = [call[0][0] for call in conn.execute.call_args_list]
    # Check that UPDATE signal_history SET escore = GREATEST... was called with penalty 0.10
    update_escore_calls = [call for call in conn.execute.call_args_list if "UPDATE signal_history SET escore =" in call[0][0]]
    assert len(update_escore_calls) > 0
    # penalty passed was Decimal("0.10")
    assert update_escore_calls[0][0][1] == Decimal("0.10")
