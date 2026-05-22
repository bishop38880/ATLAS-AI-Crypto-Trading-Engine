"""RAG Document State Machine Models.

Defines the core state transitions and data structures for the
recursive verification state machine.
"""

from __future__ import annotations

import asyncpg  # type: ignore
from redis.asyncio import Redis
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class DocumentState(str, Enum):
    """The six acyclic states of a RAG memory document."""

    RAW = "raw"
    PENDING_VERIFICATION = "pending_verification"
    VERIFIED = "verified"
    FLAGGED_REVIEW = "flagged_review"
    SOFT_DELETED = "soft_deleted"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class StateTransition:
    """Immutable record of a state transition."""

    document_id: str
    asset: str
    from_state: DocumentState
    to_state: DocumentState
    reason: str
    triggered_by: str
    escore_at_change: Optional[Decimal] = None
    contradiction_count_at_change: Optional[int] = None
    transitioned_at: Optional[datetime] = None


@dataclass(frozen=True)
class ContradictionAccumulation:
    """Immutable record of a contradiction between two documents."""

    target_document_id: str
    source_document_id: str
    asset: str
    target_decision: str
    source_decision: str
    target_score: int
    source_score: int
    time_delta_days: Decimal
    recorded_at: Optional[datetime] = None


class DocumentStateMachine:
    """Core state machine logic for RAG document lifecycles."""

    CONTRADICTION_THRESHOLD = 3
    SOFT_DELETE_AGING_DAYS = 30
    FLAGGED_REVIEW_TTL_DAYS = 7

    def __init__(self, pool: asyncpg.Pool, redis_client: Redis) -> None:
        """Initialise with asyncpg pool and redis client."""
        self._pool = pool
        self._redis = redis_client

    def _validate_transition(self, from_state: DocumentState, to_state: DocumentState) -> None:
        """Enforces strictly acyclic transitions, with one explicit exception."""
        # Exception: VERIFIED -> PENDING_VERIFICATION (contradiction loop)
        if from_state == DocumentState.VERIFIED and to_state == DocumentState.PENDING_VERIFICATION:
            return

        valid_transitions = {
            DocumentState.RAW: {DocumentState.PENDING_VERIFICATION},
            DocumentState.PENDING_VERIFICATION: {
                DocumentState.VERIFIED,
                DocumentState.FLAGGED_REVIEW,
                DocumentState.SOFT_DELETED,
            },
            DocumentState.VERIFIED: {DocumentState.ARCHIVED, DocumentState.SOFT_DELETED},
            DocumentState.FLAGGED_REVIEW: {DocumentState.SOFT_DELETED},
            DocumentState.SOFT_DELETED: {DocumentState.ARCHIVED},
            DocumentState.ARCHIVED: set(),
        }

        if to_state not in valid_transitions.get(from_state, set()):
            raise ValueError(f"Invalid transition from {from_state.value} to {to_state.value}")

    async def transition(
        self,
        document_id: str,
        asset: str,
        to_state: DocumentState,
        reason: str,
        triggered_by: str,
    ) -> None:
        """Transitions document state, updates db, logs, and clears cache."""
        from_state = await self.get_state(document_id)
        if from_state == to_state:
            return
        self._validate_transition(from_state, to_state)
        await self._execute_transition(
            document_id, asset, from_state, to_state, reason, triggered_by,
        )
        await self._redis.delete(f"rag:doc_state:{document_id}")

    async def _execute_transition(
        self, document_id: str, asset: str,
        from_state: DocumentState, to_state: DocumentState,
        reason: str, triggered_by: str,
    ) -> None:
        """Perform the DB update and log within a transaction."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT escore, contradiction_count FROM signal_history WHERE signal_id = $1",
                    document_id, timeout=5.0,
                )
                if not row:
                    raise ValueError(f"Document {document_id} not found")
                await conn.execute(
                    """
                    UPDATE signal_history
                    SET document_state = $1, state_changed_at = NOW()
                    WHERE signal_id = $2
                    """,
                    to_state.value, document_id, timeout=5.0,
                )
                await self._log_transition(
                    conn, document_id, asset, from_state, to_state,
                    reason, triggered_by, row["escore"], row["contradiction_count"],
                )

    @staticmethod
    async def _log_transition(
        conn: asyncpg.Connection, document_id: str, asset: str,
        from_state: DocumentState, to_state: DocumentState,
        reason: str, triggered_by: str,
        escore: Decimal | None, count: int | None,
    ) -> None:
        """Insert a row into the state_transition_log."""
        await conn.execute(
            """
            INSERT INTO state_transition_log
            (document_id, asset, from_state, to_state, reason, triggered_by, escore_at_change, contradiction_count_at_change)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            document_id, asset, from_state.value, to_state.value,
            reason, triggered_by, escore, count, timeout=5.0,
        )

    async def get_state(self, document_id: str) -> DocumentState:
        """Gets current state, checking Redis first then falling back to DB."""
        cache_key = f"rag:doc_state:{document_id}"
        cached = await self._redis.get(cache_key)
        if cached:
            return DocumentState(cached.decode("utf-8"))

        async with self._pool.acquire() as conn:
            state_val = await conn.fetchval(
                "SELECT document_state FROM signal_history WHERE signal_id = $1",
                document_id,
                timeout=5.0
            )
            if not state_val:
                return DocumentState.RAW

            await self._redis.setex(cache_key, 3600, state_val)
            return DocumentState(state_val)

    async def record_contradiction(self, accum: ContradictionAccumulation) -> None:
        """Records contradiction and may trigger transition to pending."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO contradiction_accumulation
                    (target_document_id, source_document_id, asset, target_decision, source_decision, target_score, source_score, time_delta_days)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    accum.target_document_id,
                    accum.source_document_id,
                    accum.asset,
                    accum.target_decision,
                    accum.source_decision,
                    accum.target_score,
                    accum.source_score,
                    accum.time_delta_days,
                    timeout=5.0
                )

                new_count = await conn.fetchval(
                    """
                    UPDATE signal_history
                    SET contradiction_count = contradiction_count + 1
                    WHERE signal_id = $1
                    RETURNING contradiction_count
                    """,
                    accum.target_document_id,
                    timeout=5.0
                )

        if new_count >= self.CONTRADICTION_THRESHOLD:
            await self.transition(
                document_id=accum.target_document_id,
                asset=accum.asset,
                to_state=DocumentState.PENDING_VERIFICATION,
                reason="contradiction_count_threshold_reached",
                triggered_by="state_machine",
            )

    async def get_documents_due_for_aging(self) -> list[str]:
        """Returns SOFT_DELETED documents exceeding aging days."""
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT signal_id FROM signal_history
                WHERE document_state = $1
                AND state_changed_at < NOW() - make_interval(days := $2)
                """,
                DocumentState.SOFT_DELETED.value,
                self.SOFT_DELETE_AGING_DAYS,
                timeout=5.0
            )
            return [r["signal_id"] for r in records]

    async def get_documents_pending_verification(self) -> list[str]:
        """Returns PENDING_VERIFICATION documents."""
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                "SELECT signal_id FROM signal_history WHERE document_state = $1",
                DocumentState.PENDING_VERIFICATION.value,
                timeout=5.0
            )
            return [r["signal_id"] for r in records]

    async def get_flagged_documents_for_expiry(self) -> list[str]:
        """Returns FLAGGED_REVIEW documents exceeding TTL days."""
        async with self._pool.acquire() as conn:
            records = await conn.fetch(
                """
                SELECT signal_id FROM signal_history
                WHERE document_state = $1
                AND state_changed_at < NOW() - make_interval(days := $2)
                """,
                DocumentState.FLAGGED_REVIEW.value,
                self.FLAGGED_REVIEW_TTL_DAYS,
                timeout=5.0
            )
            return [r["signal_id"] for r in records]
