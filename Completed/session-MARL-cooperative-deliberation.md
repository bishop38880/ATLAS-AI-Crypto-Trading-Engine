This is an incredibly robust architectural specification. The multi-agent reinforcement learning (MARL) layer is notoriously difficult to implement safely in production, but your rigid constraints—specifically the hard ±15% ceiling, the Risk Agent veto supremacy, and the strict CPU/float32 inference rules—create a highly stable sandbox.

Before giving you the prompt sequence, there is one **Technical Interrogation** point to be aware of:
* **The 40-Line Function Limit vs. Tensor Shaping:** PyTorch `forward` passes and tensor concatenation can get verbose. I will explicitly instruct the AI to use helper methods if the tensor assembly in `RevisionHead.revise()` threatens to breach the 40-line invariant. 

Here is the multi-phase prompt sequence for the **MARL Cooperative Deliberation** package. 

### The Universal Opener (Provide this to Cursor first)
**Prompt:**
> I am building POLARIS, a multi-agent cryptocurrency trading intelligence platform.
> Read the global context document fully before writing a single line of code.
> 
> INVARIANTS FOR THIS SESSION:
> 1. POLARIS has ZERO exchange awareness. No order management here.
> 2. asyncpg ONLY. All I/O async. No blocking calls. Loguru only.
> 3. Max 40 lines per function. Full type hints and docstrings.
> 4. HARD RULE — MARL runs in SHADOW MODE ONLY. The Revision Head may adjust Round 1 scores by up to ±15%. It NEVER overrides the Risk Agent veto. MARL policies are FROZEN during live trading.
> 5. CPU-only inference. No CUDA. float32 throughout.
> 6. This session creates the `atlas/marl/` package only. Do not modify existing files except `pipeline/scorer.py` and the main config.
> 
> Acknowledge these invariants before we begin Phase 1.

---

### Phase 1: Data Preparation & Injection
**Files to reference in IDE:** `atlas/marl/__init__.py` (create)
**Prompt:**
> Execute Phase 1: MARL Data Prep & Encoding.
> 
> 1. Create `atlas/marl/message_encoder.py`. Implement the `AgentMessage` frozen Pydantic model (fields: agent_name, score, max_points, normalised_score, direction_bias, confidence, had_anomaly, verdict_vector [list of 5 floats]). 
> 2. Implement the `MessageEncoder` class. Add `_infer_direction(explanations: list[str]) -> str` (bullish/bearish/neutral). Implement `encode()` to build the 5-dim vector: `[normalised_score, direction_bias (-1,0,1), confidence (1.0 or 0.5), anomaly (1 or 0), agent_type_encoding]`. Implement `encode_all()` to return alphabetically sorted messages.
> 3. Create `atlas/marl/shadow_injector.py`. Implement the `ShadowMetrics` frozen model (vwap_deviation_pct, ob_imbalance, sr_proximity_pct, atr_move_magnitude, shadow_vector [list of 4 floats], is_available, degraded_metrics).
> 4. Implement `ShadowInjector(redis_client)`. Implement `async def fetch(self, asset: str) -> ShadowMetrics` to pull the 4 keys from Redis (`shadow:{asset}:...`) via `asyncio.gather()`. On any miss, default that metric to 0.0, append its name to `degraded_metrics`, and set `is_available=False` if ALL miss. Never raise exceptions.
>
> Stop and wait for my review.

### Phase 2: The Neural Network & Trainer Stub
**Files to reference in IDE:** `atlas/marl/revision_head.py` `atlas/marl/mappo_trainer.py`
**Prompt:**
> Execute Phase 2: The PyTorch MLP & MAPPO Stub.
> 
> 1. Create `atlas/marl/revision_head.py`. Implement `RevisionHeadConfig` (input_dim=24, hidden_dim=32, output_dim=1, max_revision=0.15) and `RevisionHeadOutput` models.
> 2. Implement `RevisionHead(torch.nn.Module)`. Architecture: Linear(24->32) -> ReLU -> Linear(32->16) -> ReLU -> Linear(16->1) -> tanh.
> 3. **CRITICAL:** Implement `forward()`. It must multiply the tanh output by `max_revision`, and then wrap the final result in `torch.clamp(..., -max_revision, max_revision)` to absolutely guarantee the ±15% ceiling. Use `torch.no_grad()`. Force CPU and float32.
> 4. Implement `revise()` to handle tensor assembly. If `load_checkpoint()` fails, return a 0.0 revision output immediately without passing through the network.
> 5. Create `atlas/marl/mappo_trainer.py`. Implement `MAPPOTrainer` with a single method `train_offline()`. This method MUST raise a `NotImplementedError` stating: "MAPPO offline training requires 200+ paper trades (Phase F). Do not implement until Phase F...".

### Phase 3: Coordination, Tracking & Core Integration
**Files to reference in IDE:** `pipeline/scorer.py` (or equivalent) `atlas/config.py`
**Prompt:**
> Execute Phase 3: MARL Orchestration and Scorer Wiring.
>
> 1. Add `marl_enabled: bool = False` to `ATLASConfig` (or your main config file).
> 2. Create `atlas/marl/shadow_tracker.py`. Implement `ShadowTrackerEntry` and `ShadowTracker`. The `record()` method must write to Redis (`marl:shadow:{asset}:latest`, TTL 1800s) AND execute an `asyncpg` INSERT into `marl_shadow_log` (schema matching the spec). Never raise on write failure.
> 3. Create `atlas/marl/deliberation.py`. Implement `DeliberationResult` and `DeliberationCoordinator`.
> 4. **CRITICAL:** In `DeliberationCoordinator.deliberate()`, the VERY FIRST check must be `if risk_veto: return round1_weighted_score`. If not vetoed, run the encoding, fetch shadow metrics, execute the Revision Head for the technical agent, recompute the sum, write to `shadow_tracker`, and return the result. Wrap in a try-except; on any exception, return `round1_weighted_score`.
> 5. Open `pipeline/scorer.py`. At the end of the `score()` method, add the MARL deliberation pass. It must be gated by `if self._config.marl_enabled and not risk_veto:`.

### Phase 4: Quality Gates & Testing
**Files to reference in IDE:** `atlas/marl/test_marl.py` 
**Prompt:**
> Execute Phase 4: The Test Suite.
>
> Create `atlas/marl/test_marl.py` and write these exact 12 async `pytest` tests:
> 1. `test_message_encoder_vector_length`: Vector length is exactly 5.
> 2. `test_message_encoder_direction`: Bullish keywords -> 1.0 encoding.
> 3. `test_shadow_injector_success`: All keys present -> `is_available=True`.
> 4. `test_shadow_injector_missing`: All keys missing -> `is_available=False`, vector=[0,0,0,0].
> 5. `test_revision_head_no_checkpoint`: Missing checkpoint safely yields 0.0 revision.
> 6. `test_revision_head_clamping`: Forward pass output strictly bounded by [-0.15, 0.15].
> 7. `test_deliberation_risk_veto`: `risk_veto=True` skips deliberation entirely.
> 8. `test_deliberation_success`: Non-zero revision alters `round2_total`.
> 9. `test_deliberation_exception_safety`: Coordinator throws exception -> returns `round1_total` safely.
> 10. `test_revision_head_extreme_weights`: Manually inject massive weights into MLP, verify `revise()` still clamps to ±15%.
> 11. `test_shadow_tracker_record`: Mocks `asyncpg` and Redis writes successfully.
> 12. `test_mappo_trainer_stub`: Verify `train_offline()` raises `NotImplementedError`.
>
> Ensure zero strict typing errors. Do not stop until all 12 are written.

---

### Execution Strategy
1. Paste the Universal Opener. Let the AI acknowledge it.
2. Run Phases 1 through 4 sequentially. 
3. After Phase 4 finishes, run the bash grep commands you included in your Quality Gates spec to manually verify the hard limits (`clamp` and `risk_veto`) were properly embedded in the generated code.