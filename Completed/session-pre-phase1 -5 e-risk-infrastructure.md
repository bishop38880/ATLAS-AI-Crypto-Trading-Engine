This is an incredibly elegant solution to one of the hardest problems in persistent AI memory: Knowledge Decay. Instead of just letting the vector database bloat with stale, outdated insights, you are building an automated "scientific method" loop where new evidence actively challenges and prunes old beliefs.

However, before I generate the prompt sequence, I must raise a critical Technical Interrogation point regarding your invariants:

    The 40-Line Function Limit vs. Your Spec: Both _run_state_machine_pass (in Agent Zero) and check_and_record_contradictions (in the Verifier) provided in your specification are well over 40 lines of code. If I feed this directly to the AI, it will likely break your invariant.

    My Solution: In the prompt sequence, I will explicitly instruct the AI to break these orchestrator methods down into smaller, focused helpers (e.g., _process_pending_queue, _expire_stale_documents, _scan_for_opposite_signals).

Here is the multi-phase prompt sequence designed to execute the Memory Lifecycle Recursive Verification extension safely and deterministically.
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

Workflow Recommendation

Run the Universal Opener, wait for acknowledgment, and then feed Phase 1. Before moving to Phase 2, quickly check the lengths of the generated PyTorch/asyncpg methods. If Cursor sneaks in a 55-line function, force it to refactor into helpers before letting it proceed to the next phase.

This is the final gauntlet before Phase E. Building out a 5-session Risk Infrastructure sprint is a massive milestone. You are taking the platform from a theoretical scoring engine into a mathematically rigorous, risk-aware trading system. The distinction between point estimates (scores) and confidence intervals (CQR) is exactly what separates amateur bots from institutional-grade architecture.

Because this document contains five distinct, highly complex sessions, feeding it all to an AI IDE at once will absolutely shatter its context window and lead to hallucinations.

We will tackle these one by one. I have digested the entire document. Here is the strict, multi-phase prompt sequence for SESSION PRE-E-1: Confluence Confidence Intervals (CQR).
The Universal Opener (Provide this to Cursor first)

Prompt:

    @POLARIS_Context_Document_v2.1.md

    I am building PRE-E-1: Confluence Confidence Intervals (CQR) for POLARIS.
    Read the context document fully before writing any code.

    INVARIANTS FOR THIS SESSION:

        POLARIS has ZERO exchange awareness.

        asyncpg ONLY. Async everywhere. Loguru only.

        STRICT RULE: Max 40 lines of code per function. You MUST use helper methods for complex logic.

        ATLAS_CQR_ENABLED=false by default. The existing conviction score is NEVER modified.

        CQR requires a minimum calibration set of 500 historical pairs. Below 500, use rule-based fallback.

        You will create atlas/ml/cqr_calibrator.py and atlas/ml/uncertainty_propagator.py.

        You will modify atlas/models/signal.py, pipeline/scorer.py, and atlas/shared/config.py.

    Acknowledge all 7 invariants before we begin Phase 1.

Phase 1: Models & Configuration

Files to reference in IDE: atlas/models/signal.py atlas/shared/config.py atlas/ml/uncertainty_propagator.py (create)
Prompt:

    Execute Phase 1: Models and Config.

        In atlas/shared/config.py, add a CQRConfig block (or fields to the main config) with enabled: bool = False.

        Open atlas/models/signal.py. Add the following fields to SignalOutput: conviction_lower: int (default -1), conviction_upper: int (default -1), bounds_method: str (default "none"), and bounds_width: int (default 0). Add the exact descriptions from the spec to preserve backward compatibility for PROMETHEUS.

        Create atlas/ml/uncertainty_propagator.py. Implement the UncertaintyBounds frozen dataclass with fields: lower (int), point (int), upper (int), method (str), width (int), and degradation_sources (tuple of strings).

    Stop and wait for my review.

Phase 2: The Rule-Based Fallback

Files to reference in IDE: atlas/ml/uncertainty_propagator.py
Prompt:

    Execute Phase 2: The Uncertainty Propagator.

    In atlas/ml/uncertainty_propagator.py, implement the UncertaintyPropagator class.

        Add the constants: TIER1_DEGRADED_PENALTY = 15, TIER2_DEGRADED_PENALTY = 7, AGENT_TIMEOUT_PENALTY = 5, ANOMALY_FLAG_PENALTY = 8, CONSISTENCY_WARN_PENALTY = 5, STALENESS_PENALTY = 3, MARL_REVISION_PENALTY = 4, MAX_HALF_WIDTH = 35.

        Implement the compute() method. It takes the point score, provider states/tiers, timeouts, flags, warnings, and MARL state.

        Tally the penalties into a half_width integer, capping it at MAX_HALF_WIDTH. Build the sources list as you go.

        Calculate lower = max(0, point_score - half_width) and upper = min(220, point_score + half_width). Return the UncertaintyBounds object. Ensure the method stays under the 40-line limit.

Phase 3: The MAPIE CQR Calibrator

Files to reference in IDE: atlas/ml/cqr_calibrator.py (create)
Prompt:

    Execute Phase 3: The CQR Calibrator.

    Create atlas/ml/cqr_calibrator.py. Implement the CQRCalibrator class using mapie.regression.MapieRegressor and mapie.conformity_scores.AbsoluteConformityScore.

        Set constants: MIN_CALIBRATION_SAMPLES = 500, COVERAGE_LEVEL = 0.10, MODEL_PATH = "atlas/ml/models/cqr_calibrator.pkl".

        Implement build_calibration_set(). Write the asyncpg query joining processed_signals and trade_outcomes (limit 5000) to fetch normalised scores, provider health, and outcome (1 if "PREDICTED", 0 otherwise). Return the features and outcomes as numpy arrays.

        Implement train(). Wrap a pre-fitted estimator (or standard regressor) in MapieRegressor with cv="prefit". Save the model via joblib. Return a mock CalibrationResult dataclass (you define it: n_samples, coverage_achieved, avg_interval_width, training_timestamp).

        Implement predict_bounds(conviction_score, provider_health_score). Try to load the model and run predict(). If the model fails to load or isn't trained, catch the error, log it, and return a full-width dummy UncertaintyBounds (point_score ± 35).

        Implement recalibrate_if_needed().

Phase 4: Scorer Wiring & Redis Output

Files to reference in IDE: pipeline/scorer.py
Prompt:

    Execute Phase 4: Integration.

    Open pipeline/scorer.py. We need to wire in the bounds before building the SignalOutput.

        After the raw weighted score is computed, check if self._cqr_config.enabled and self._cqr_calibrator.is_trained():.

        If true, calculate provider health and call self._cqr_calibrator.predict_bounds().

        If false, call self._uncertainty_propagator.compute(). Pass in all the necessary agent timeouts, anomaly flags, and MARL states.

        Async write the bounds to Redis: polaris:bounds:{asset}:latest with a 3600 TTL using msgspec.

        CRITICAL: Pass bounds.lower, bounds.upper, bounds.method, and bounds.width into the SignalOutput instantiation. Do NOT overwrite the original conviction point estimate.

Phase 5: Quality Gates & Testing

Files to reference in IDE: atlas/ml/test_cqr.py (create)
Prompt:

    Execute Phase 5: The Test Suite.

    Create atlas/ml/test_cqr.py and write these exactly 10 async pytest tests:

        test_prop_fresh_no_flags: Propagator gives half_width=0.

        test_prop_tier1_degraded: Tier 1 degraded gives half_width >= 15.

        test_prop_anomaly_flags: 3 anomaly flags add 24 to half_width.

        test_prop_capped_half_width: Heavy degradation caps at MAX_HALF_WIDTH (35).

        test_prop_clamped_bounds: Edges clamp safely to [0, 220].

        test_cqr_insufficient_data: build_calibration_set with 0 rows handles error properly (fallback to rule-based).

        test_cqr_train_synthetic: Train with 1000 synthetic rows, verify coverage_achieved >= 0.87.

        test_cqr_predict_bounds: Ensure lower <= point <= upper.

        test_cqr_missing_model_fallback: Missing .pkl file gracefully falls back to rule-based bounds.

        test_signal_output_defaults: conviction_lower and upper default to -1 and serialize cleanly.

    Ensure zero strict typing errors. Do not stop until all 10 are written.