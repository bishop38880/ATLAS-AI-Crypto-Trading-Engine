The Universal Opener (Provide this to Cursor first)

Prompt:

    @POLARIS_Context_Document_v2.1.md

    I am extending the POLARIS Agent Zero memory lifecycle manager with a recursive verification state machine.
    Read the context document fully before writing any code.

    INVARIANTS FOR THIS SESSION:

        POLARIS has ZERO exchange awareness.

        asyncpg ONLY. Async everywhere. Loguru only.

        STRICT RULE: Max 40 lines of code per function. You MUST use helper methods for complex logic.

        ATLAS_RECURSIVE_VERIFY_ENABLED=false by default. Zero impact when disabled.

        The state machine must be persisted in PostgreSQL. A Redis outage must not cause memory loss.

        HARD RULE: The state machine is strictly acyclic: RAW → PENDING_VERIFICATION → VERIFIED/FLAGGED/SOFT_DELETED → ARCHIVED. The only "loop" is when contradiction_count >= 3 forces a VERIFIED document back to PENDING_VERIFICATION.

        This session modifies schema.sql, atlas/rag/agent_zero.py, and atlas/rag/verifier.py. It creates atlas/rag/state_machine.py and atlas/rag/test_state_machine.py.

    Acknowledge all 7 invariants before we begin Phase 1.

Phase 1: Database Schema & Data Structures

Files to reference in IDE: schema.sql atlas/rag/state_machine.py (create)
Prompt:

    Execute Phase 1: Schema and Models.

        Update schema.sql. Add the new columns to rag_signal_memory: document_state (TEXT, default 'raw'), contradiction_count (INTEGER, default 0), escore (NUMERIC(5,4)), and state_changed_at (TIMESTAMPTZ).

        Add the two new tables to schema.sql: state_transition_log and contradiction_accumulation, matching the exact schemas and indexes from the specification. Add the idx_rag_signal_memory_state index.

        Create atlas/rag/state_machine.py. Implement the DocumentState Enum (raw, pending_verification, verified, flagged_review, soft_deleted, archived).

        Implement the StateTransition and ContradictionAccumulation frozen dataclasses.

    Stop and wait for my review once these structural files are complete.

Phase 2: The State Machine Core

Files to reference in IDE: atlas/rag/state_machine.py
Prompt:

    Execute Phase 2: The DocumentStateMachine.

    In atlas/rag/state_machine.py, implement the DocumentStateMachine class.

        Define the class constants: CONTRADICTION_THRESHOLD = 3, SOFT_DELETE_AGING_DAYS = 30, FLAGGED_REVIEW_TTL_DAYS = 7.

        Implement _validate_transition(from_state, to_state). It must strictly enforce the acyclic rules and raise a ValueError for illegal transitions (like VERIFIED -> RAW, or SOFT_DELETED -> VERIFIED).

        Implement transition(). It must call _validate_transition, execute the asyncpg INSERT into state_transition_log, UPDATE the rag_signal_memory table, and invalidate the Redis cache for that document.

        Implement get_state(). Try Redis rag:doc_state:{document_id} first, then fallback to asyncpg.

        Implement record_contradiction(). Insert into contradiction_accumulation, update contradiction_count in rag_signal_memory. CRITICAL: If the new count >= CONTRADICTION_THRESHOLD, automatically call self.transition() to move the target document to PENDING_VERIFICATION with reason "contradiction_count_threshold_reached".

        Implement the three async getter methods for the nightly job: get_documents_due_for_aging, get_documents_pending_verification, and get_flagged_documents_for_expiry.

Phase 3: Agent Zero Nightly Integration

Files to reference in IDE: atlas/rag/agent_zero.py atlas/rag/state_machine.py
Prompt:

    Execute Phase 3: Agent Zero Nightly Integration.

    Open atlas/rag/agent_zero.py. We need to add the _run_state_machine_pass method.
    CRITICAL INVARIANT: To stay under the 40-line limit, you MUST break this into three helper methods:

        _process_pending_queue(): Iterates over pending documents, runs the verifier, calculates Escore (applying min(contradiction_count * 0.05, 0.20) penalty), and transitions to VERIFIED, SOFT_DELETED, or FLAGGED_REVIEW based on the result.

        _expire_flagged_documents(): Transitions stale FLAGGED_REVIEW docs to SOFT_DELETED.

        _age_soft_deleted_documents(): Transitions old SOFT_DELETED docs to ARCHIVED and notifies the _rag_pipeline to update Qdrant.

    Create the StateMachinePassResult dataclass to aggregate the counts from these helpers. Then, implement _run_state_machine_pass() as a small orchestrator that simply awaits these three helpers and returns the StateMachinePassResult.

Phase 4: Verifier Contradiction Detection

Files to reference in IDE: atlas/rag/verifier.py atlas/rag/state_machine.py
Prompt:

    Execute Phase 4: Verifier Integration.

    Open atlas/rag/verifier.py. Add check_and_record_contradictions().
    CRITICAL INVARIANT: To stay under the 40-line limit, split this logic into:

        _scan_for_opposite_signals(asset, new_direction, timestamp): Handles the Qdrant scroll query looking back 14 days for VERIFIED documents >= the min score.

        check_and_record_contradictions(...): Checks if the new document is CLEAN and directional (Buy/Sell). If so, it calls _scan_for_opposite_signals. Iterate over the results, calculate time_delta_days, and call state_machine.record_contradiction for valid opposites.

    This ensures Qdrant query logic is separated from the business logic of recording the contradiction.

Phase 5: Quality Gates & Testing

Files to reference in IDE: atlas/rag/test_state_machine.py
Prompt:

    Execute Phase 5: The Test Suite.

    Create atlas/rag/test_state_machine.py and write these exactly 10 async pytest tests:

        test_valid_transition_flow: RAW -> PENDING -> VERIFIED.

        test_invalid_transition_verified_to_raw_raises: Enforce acyclic rule.

        test_invalid_transition_soft_deleted_to_verified_raises: Enforce permanent soft-delete.

        test_invalid_transition_from_archived_raises: Enforce terminal state.

        test_contradiction_threshold_triggers_reverification: Reaching count=3 forces transition to PENDING_VERIFICATION.

        test_flagged_review_auto_expires: Documents older than 7 days transition to SOFT_DELETED.

        test_soft_deleted_aging: Documents older than 30 days transition to ARCHIVED.

        test_contradiction_recorded_against_opposite_direction: New VERIFIED strong buy records contradiction against VERIFIED strong sell within 14d.

        test_low_score_document_no_propagation: Score < 140 doesn't trigger contradiction logic.

        test_contradiction_penalty_reduces_escore: Verify contradiction_count=3 reduces Escore by 0.15 during Agent Zero run.

    Ensure zero strict typing errors. Do not stop until all 10 are written.
```
