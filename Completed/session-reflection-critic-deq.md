The Universal Opener (Provide this to Cursor first)

Prompt:

    @POLARIS_Context_Document_v2.1.md

    I am building the POLARIS Reflection LM/Critic module with DEQ convergence guarantees and WBFT.
    Read the context document fully before writing any code.

    INVARIANTS FOR THIS SESSION:

        POLARIS has ZERO exchange awareness.

        asyncpg ONLY. Async everywhere. Loguru only.

        STRICT RULE: Max 40 lines of code per function. You MUST use helper methods for complex logic.

        ATLAS_REFLECTION_ENABLED=false by default. Zero impact when disabled.

        HARD CONVERGENCE RULE: Maximum 2 reflection rounds. Round 2 is ALWAYS final. 5-second DEQ hard timeout.

        The Reflection LM uses DeepSeek V3 (not R1).

        WBFT divergence check: >25 point divergence between Round 1 and Round 2 flags for human review and uses Round 1.

        Reflection can never raise the score above the original score, and cannot drop below 55.

        You will create pipeline/reflection.py and pipeline/test_reflection.py. Minimal modifications to output_processor.py and config.

    Acknowledge all 9 invariants before we begin Phase 1.

Phase 1: Configuration, Models & Schema

Files to reference in IDE: atlas/shared/config.py pipeline/reflection.py (create) schema.sql (if applicable)
Prompt:

    Execute Phase 1: Models, Config, and Schema.

        In your configuration file (atlas/shared/config.py), add the ReflectionConfig block using pydantic-settings (env_prefix="ATLAS_REFLECTION_"). Fields: enabled (False), score_threshold (160), deq_timeout_s (5.0), deq_max_rounds (2), deq_convergence_pts (11), wbft_divergence_pts (25), model ("deepseek-chat"), shadow_mode (True), max_score_reduction (15), min_score_floor (55).

        Create pipeline/reflection.py. Implement the following frozen dataclasses exactly as specified:

            WBFTAgentWeight

            DEQCheckpoint

            ReflectionResult

        Write the PostgreSQL schema additions for reflection_log and human_review_queue as raw SQL comments at the top of the file (or inject them into schema.sql if you have it open), including the idx_human_review_pending index.

    Stop and wait for my review once these structural elements are complete.

Phase 2: WBFT Weighter & DEQ Solver

Files to reference in IDE: pipeline/reflection.py
Prompt:

    Execute Phase 2: The Deterministic Engines (WBFT and DEQ).

    In pipeline/reflection.py:

        Implement WBFTWeighter. Add the default constants (Sharpe=0.0, Quality=0.8, Timeout=0.0).

        Implement async def compute_weights(self, agent_results). Fetch the 3 trailing metrics from Redis for each agent (wbft:agent:{name}:...). Calculate raw_weight, then normalize across all agents so they sum to 1.0. Handle Redis misses safely using defaults.

        Implement format_wbft_context(...) to build the readable string of agent verdicts + weights for the LLM prompt.

        Implement DEQSolver. It must take max_rounds, convergence_pts, and timeout_s in __init__.

        Implement should_run_next_round(...). Return (False, "timeout") if elapsed >= timeout, (False, "max_rounds") if checkpoints > max_rounds, and (False, "converged") if the last two checkpoints are within the convergence threshold. Otherwise, return (True, "continue").

        Implement measure_convergence() and measure_wbft_divergence() helper methods.

Phase 3: The Reflection LM/Critic

Files to reference in IDE: pipeline/reflection.py
Prompt:

    Execute Phase 3: The ReflectionCritic.

    In pipeline/reflection.py, implement the ReflectionCritic class.
    CRITICAL INVARIANT: To stay under the 40-line limit, you MUST break the orchestration down.

        Implement _build_reflection_prompt(). Format the WBFT agent verdicts, primary synthesis, adversarial result, and verifier report.

        Implement _parse_reflection_output(). Safely extract CONFIDENCE_ADJUSTMENT (clamped to [-config.max_score_reduction, 0]), CRITICAL_NOTES, and UNSUPPORTED_CLAIM items. Return defaults on failure.

        Implement _select_final_checkpoint(). If wbft_diverged is True, return Round 1. Otherwise, return the last completed round.

        Implement async def _write_human_review_queue(). Push to Redis polaris:review_queue and insert into human_review_queue table via asyncpg.

        Finally, implement the main orchestrator: async def reflect(...). Wrap the loop in an asyncio.wait_for (or track elapsed_s strictly) to enforce the 5.0s deq_timeout_s. Ensure the final score never exceeds original_score and never drops below min_score_floor (55). ONLY use the "deepseek-chat" model identifier.

Phase 4: Output Processor Integration

Files to reference in IDE: pipeline/output_processor.py
Prompt:

    Execute Phase 4: Pipeline Wiring.

    Open pipeline/output_processor.py (or your primary scoring orchestrator).

        Add the Reflection pass call immediately after the deterministic verification and adversarial passes are complete.

        Gate it strictly with: if self._config.reflection.enabled:.

        Pass the assembled_context, primary_synthesis, adversarial_result, verifier_report, and agent_results into ReflectionCritic.reflect().

        If ReflectionResult.triggered is True and shadow_mode is False, apply the final_score to the ProcessedSignal and append the critical_notes. If shadow_mode is True, log the result but do not alter the signal.

Phase 5: Quality Gates & Testing

Files to reference in IDE: pipeline/test_reflection.py (create)
Prompt:

    Execute Phase 5: The Test Suite.

    Create pipeline/test_reflection.py and write these exactly 12 async pytest tests:

        test_reflection_disabled: enabled=False returns non-triggered result instantly.

        test_below_threshold_skips: Score < 160 is not triggered.

        test_deq_timeout_uses_round0: Simulate 5s timeout, verify timed_out=True and it safely returns Round 0 data.

        test_deq_converges_round1: Round 1 delta < 11 pts -> stops at Round 1, no Round 2 runs.

        test_deq_diverges_runs_round2: Round 1 delta > 11 pts -> correctly triggers Round 2.

        test_wbft_divergence_flags_human: Round 2 diverges from R1 > 25 pts -> wbft_diverged=True, returns Round 1, flags human review.

        test_max_rounds_invariant: Verify rounds_run never exceeds 2.

        test_ceiling_invariant: Final score never exceeds original score.

        test_floor_invariant: Final score never drops below 55.

        test_llm_parsing: Verify _parse_reflection_output correctly extracts negative adjustments and claims.

        test_wbft_normalisation: Verify agent weights strictly sum to 1.0.

        test_wbft_redis_miss_defaults: Missing Redis data yields correct default neutral weights.

    Ensure zero strict typing errors. Do not stop until all 12 are written.