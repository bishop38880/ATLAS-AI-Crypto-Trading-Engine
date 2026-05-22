"""Nightly state manager for Agent Zero."""

from __future__ import annotations

from loguru import logger
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from qdrant_client.models import PointIdsList

from atlas.rag.state_machine import DocumentState, DocumentStateMachine
from atlas.rag.verifier import DocumentVerifier, VerificationStatus




@dataclass(frozen=True)
class StateMachinePassResult:
    """Aggregates counts from the nightly state machine pass."""

    verified_count: int
    soft_deleted_count: int
    flagged_count: int
    archived_count: int
    errors: int


class _DocWrapper:
    """Typed wrapper for signal_history rows passed to verifier."""

    id: str
    decision: str
    asset: str
    timestamp: Any
    confluence_score: Any
    reasoning_summary: str
    key_convergences: list[Any]
    key_risks: list[Any]

    def __init__(self, **kw: Any) -> None:
        for k, v in kw.items():
            setattr(self, k, v)


class AgentZeroStateManager:
    """Nightly state machine pass for document verification and aging."""

    def __init__(
        self,
        pool: Any,
        state_machine: DocumentStateMachine,
        verifier: DocumentVerifier,
        qdrant_client: Any,
        qdrant_collection: str,
    ) -> None:
        self._pool = pool
        self._sm = state_machine
        self._verifier = verifier
        self._qdrant = qdrant_client
        self._collection = qdrant_collection

    async def _run_state_machine_pass(self) -> StateMachinePassResult:
        """Orchestrator for the nightly state machine pass."""
        import asyncio
        pending_task = asyncio.create_task(self._process_pending_queue())
        expire_task = asyncio.create_task(self._expire_flagged_documents())
        age_task = asyncio.create_task(self._age_soft_deleted_documents())
        
        (v, s, f, e1), (s2, e2), (a, e3) = await asyncio.gather(pending_task, expire_task, age_task)

        return StateMachinePassResult(
            verified_count=v,
            soft_deleted_count=s + s2,
            flagged_count=f,
            archived_count=a,
            errors=e1 + e2 + e3,
        )

    async def _process_pending_queue(self) -> tuple[int, int, int, int]:
        """Processes documents in PENDING_VERIFICATION state."""
        docs = await self._sm.get_documents_pending_verification()
        v, s, f, e = 0, 0, 0, 0
        for doc_id in docs:
            try:
                res = await self._process_single_pending_doc(doc_id)
                if res == DocumentState.VERIFIED: v += 1
                elif res == DocumentState.SOFT_DELETED: s += 1
                elif res == DocumentState.FLAGGED_REVIEW: f += 1
            except Exception as exc:
                logger.error("Error processing pending document | doc_id={} | error={}", doc_id, str(exc))
                e += 1
        return v, s, f, e

    async def _process_single_pending_doc(self, doc_id: str) -> DocumentState | None:
        """Runs the verifier on a single document and updates escore/state."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM signal_history WHERE signal_id = $1", doc_id, timeout=5.0)
            if not row:
                return None

            doc = _DocWrapper(
                id=row["signal_id"], decision=row["decision"], asset=row["asset"],
                timestamp=row["created_at"], confluence_score=row["raw_score"],
                reasoning_summary=row["reasoning"],
                key_convergences=row.get("metadata", {}).get("key_convergences", []),
                key_risks=row.get("metadata", {}).get("key_risks", [])
            )

            report = await self._verifier.verify(doc)
            await self._verifier.check_and_record_contradictions(doc, report, self._sm)

            ccount = await conn.fetchval("SELECT contradiction_count FROM signal_history WHERE signal_id = $1", doc_id, timeout=5.0)
            penalty = min(Decimal(ccount) * Decimal("0.05"), Decimal("0.20"))
            await conn.execute("UPDATE signal_history SET escore = GREATEST(0, COALESCE(escore, 0) - $1) WHERE signal_id = $2", penalty, doc_id, timeout=5.0)

            status_map = {
                VerificationStatus.CLEAN: DocumentState.VERIFIED,
                VerificationStatus.FLAGGED: DocumentState.FLAGGED_REVIEW,
                VerificationStatus.TIMESTAMP_ANOMALY: DocumentState.FLAGGED_REVIEW,
                VerificationStatus.INCOHERENT: DocumentState.FLAGGED_REVIEW,
            }
            new_state = status_map.get(report.status, DocumentState.SOFT_DELETED)
            await self._sm.transition(doc_id, doc.asset, new_state, "verification_pass", "state_machine")
            return new_state

    async def _expire_flagged_documents(self) -> tuple[int, int]:
        """Transitions stale FLAGGED_REVIEW docs to SOFT_DELETED."""
        docs = await self._sm.get_flagged_documents_for_expiry()
        s, e = 0, 0
        for doc_id in docs:
            try:
                async with self._pool.acquire() as conn:
                    asset = await conn.fetchval("SELECT asset FROM signal_history WHERE signal_id = $1", doc_id, timeout=5.0)
                    if asset:
                        await self._sm.transition(doc_id, asset, DocumentState.SOFT_DELETED, "flagged_review_expired", "state_machine")
                        s += 1
            except Exception as exc:
                logger.error("Error expiring flagged doc | doc_id={} | error={}", doc_id, str(exc))
                e += 1
        return s, e

    async def _age_soft_deleted_documents(self) -> tuple[int, int]:
        """Transitions old SOFT_DELETED docs to ARCHIVED and notifies Qdrant."""
        docs = await self._sm.get_documents_due_for_aging()
        a, e = 0, 0
        for doc_id in docs:
            try:
                async with self._pool.acquire() as conn:
                    asset = await conn.fetchval("SELECT asset FROM signal_history WHERE signal_id = $1", doc_id, timeout=5.0)
                    if asset:
                        await self._sm.transition(doc_id, asset, DocumentState.ARCHIVED, "soft_deleted_aged_out", "state_machine")
                        await self._qdrant.set_payload(
                            collection_name=self._collection,
                            payload={"archived": True},
                            points=PointIdsList(points=[doc_id]),
                        )
                        a += 1
            except Exception as exc:
                logger.warning("Failed to update Qdrant for archived doc | doc_id={} | error={}", doc_id, str(exc))
                e += 1
        return a, e
