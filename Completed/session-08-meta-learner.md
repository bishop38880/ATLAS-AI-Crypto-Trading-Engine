# SESSION 08 — Stacking Meta-Learner: Replace Fixed Weights with Learned Fusion

## Context Files
@pipeline/scorer.py @agents/base.py @pipeline/orchestrator.py @atlas/shared/config.py

## Prerequisites

**Direct prerequisites (required before this session runs):**
Sessions 01–07 complete. Signal schema v2 (including `signal_id`) from Session 00 must be in place.

**Soft prerequisite (required only for meta-learner activation):**
Session 11A (Post-Trade Learning MCP) populates the `trade_signals.pnl_pct` column that this meta-learner trains on. **This session can be built and shipped BEFORE Session 11A runs** — the meta-learner will correctly detect `< 200` training samples and fall back to fixed weights. The meta-learner becomes active once Session 11A has accumulated enough closed-trade outcomes.

**Circular-dependency note:** Earlier versions of this session's prerequisites block said "Session 11A must be complete." That was misleading — if the runner follows numerical order, 08 runs before 11A by design. The fallback path (Task 2's `InsufficientDataError`) is the graceful handling. This clarified wording prevents Antigravity from refusing to start the session.

## Goal
Replace the fixed weighted-sum in `ConfluenceScorer` with a stacking
meta-learner that treats the 10 agent outputs as features and learns
optimal combination weights from historical signal-outcome data.
This is the single highest-impact accuracy improvement in the roadmap.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`. All env vars read through `PolarisSettings`.
4. **40-line function limit.** Extract helpers for anything longer.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.** No orders, positions, credentials, or execution logic.
7. **Test floor is sacred.** `pytest` count must not decrease.
8. **THE ASYNC ML BOUNDARY:** `scikit-learn` is synchronous and CPU-bound. You **MUST NEVER** execute `.fit()`, `.predict()`, `.predict_proba()`, or any sklearn method directly on the async event loop. **ALL** sklearn calls must be wrapped in `await asyncio.to_thread(...)`. Failure to do this will block the uvloop thread and destroy the 50ms latency target.
9. **TimeSeriesSplit ONLY.** You **MUST** use `sklearn.model_selection.TimeSeriesSplit` with `n_splits=5`. Using `train_test_split`, random `KFold`, or any other random splitting strategy is **DATA LEAKAGE** — it leaks future price data into the past and invalidates the model. This is mathematically forbidden.
10. **Model serialization:** Use `msgspec` for metadata. For the sklearn model object itself, store coefficients as plain dicts. **Never use `pickle` or `joblib`** — they are banned. Store learned coefficients as a `msgspec`-encoded dict, and reconstruct the model from coefficients at load time.
11. **Numpy/Scipy exception:** ML modules are permitted to use `numpy` and `scipy` for math operations. All ML outputs (`np.float64`) **must be cast back to native Python `float`** before exiting the module boundary. No numpy types leak into Pydantic models or Redis.
12. **Graceful insufficient-data fallback.** The meta-learner MUST handle the case where Session 11A has not yet run. Raise `InsufficientDataError` from the training builder, catch it at the scorer boundary, and fall back to fixed weights. Log WARN once per cycle so the mode is visible; do NOT log on every agent call.

---

## Task 1 — Training Data Pipeline

Create `atlas/ml/training_data.py`:

- Class `TrainingDataBuilder`
- Queries PostgreSQL (via `asyncpg` — never SQLAlchemy) for historical signal records that have outcome data (populated by the Post-Trade Learning MCP, Session 11A)
- Joins `signal_history` with `trade_signals.pnl_pct` using `signal_id` as the correlation key (from Session 00's schema)
- Builds a feature matrix where each row is one signal cycle:
  - Features (X): 10 agent scores (one per agent), normalised 0–1
  - Label (y): binary outcome — 1 if trade was profitable, 0 if not (derived from `pnl_pct` field in outcome data)
- Method: `async def build_dataset(min_samples: int = 200) -> tuple[np.ndarray, np.ndarray]`
- Returns (X, y) numpy arrays
- If fewer than `min_samples` available, raise `InsufficientDataError` and fall back to fixed weights
- Use `asyncpg` pool for DB queries, not raw connections

## Task 2 — Stacking Meta-Learner

Create `atlas/ml/meta_learner.py`:

- Class `StackingMetaLearner`
- Uses `sklearn.linear_model.LogisticRegressionCV` as the meta-model
- **CRITICAL:** Walk-forward cross-validation using `sklearn.model_selection.TimeSeriesSplit` with `n_splits=5`. **NEVER** use random splits for time-series data. If you write `train_test_split` or `KFold`, you have introduced data leakage and the model is invalid.
- Method: `def train(X: np.ndarray, y: np.ndarray) -> MetaLearnerResult`
  - Fits the model
  - Returns: `MetaLearnerResult(coefficients: dict[str, float], cv_score: float, n_samples: int)`
  - Coefficients map agent names to learned weights
  - **All `np.float64` values must be cast to `float()` before entering the Pydantic model**
- Method: `def predict(agent_scores: dict[str, float]) -> float`
  - Returns probability (0–1) that this signal combination is profitable
  - **Return type must be native Python `float`, not `np.float64`**
- Model persistence: Serialize coefficients as a `msgspec`-encoded dict to `atlas/ml/models/meta_learner_coefficients.json`. At load time, reconstruct `LogisticRegressionCV` from stored coefficients. **Do NOT use `pickle` or `joblib`** — they are banned.

**ASYNC BOUNDARY ENFORCEMENT:**
```python
# CORRECT — wrapped in to_thread
result = await asyncio.to_thread(self.meta_learner.train, X, y)
prediction = await asyncio.to_thread(self.meta_learner.predict, scores)

# FATAL ERROR — blocks event loop
result = self.meta_learner.train(X, y)  # NEVER DO THIS IN ASYNC CONTEXT
```

**Insufficient-data fallback path:**
```python
try:
    X, y = await training_builder.build_dataset(min_samples=200)
except InsufficientDataError:
    # Expected when Session 11A hasn't accumulated enough closed trades yet.
    # Scorer will fall back to fixed weights. Log once per cycle, not per call.
    if not self._insufficient_data_logged:
        logger.warning(
            "meta_learner_insufficient_data_fallback_to_fixed_weights",
            min_required=200,
        )
        self._insufficient_data_logged = True
    return None  # signals fallback to fixed weights
```

## Task 3 — Integration with ConfluenceScorer

Update `ConfluenceScorer`:

- Add `meta_learner: StackingMetaLearner | None` parameter
- If meta_learner is trained and available:
  - Use `await asyncio.to_thread(meta_learner.predict, agent_scores)` as the primary score
  - Multiply by 100 to get 0–100 conviction
  - STILL apply hard-point threshold check as safety override
  - The 220-point confluence scoring system must not be modified by the meta-learner — it provides an alternative scoring path, not a modifier
- If meta_learner is None or has insufficient data:
  - Fall back to existing fixed-weight scoring (current behaviour)
- Log which scoring mode was used: `"meta_learner"` or `"fixed_weights"`

**SCORER DAG NOTE:** This session establishes the meta-learner as Step 1 in the scorer's order of operations. Sessions 09 (Regime), 10 (Decorrelation), and 09B (News Suppression) layer additional adjustments. The final DAG is:
1. **Primary:** Meta-Learner prediction (if trained, >200 samples)
2. **Fallback:** HMM Regime Weights → PCA Decorrelation → weighted sum (if no meta-learner)
3. **Suppression:** News/Macro conviction suppression (applied AFTER base score)
4. **Shadow:** Thompson Sampling (logged only, not applied unless `THOMPSON_LIVE=True`)

## Task 4 — Retraining Schedule

Create `atlas/ml/retraining.py`:

- Function: `async def retrain_if_needed(min_new_outcomes: int = 50) -> bool`
- Checks how many new outcomes have arrived since last training (query PostgreSQL via `asyncpg`)
- If >= `min_new_outcomes` new outcomes:
  - Rebuild dataset via `TrainingDataBuilder`
  - Retrain via `await asyncio.to_thread(meta_learner.train, X, y)`
  - Save coefficients via `msgspec.json.encode`
- Run this check at the start of each analysis cycle (cheap DB count query)
- Log retraining events with: n_samples, cv_score, top_3_coefficients

## Quality Gates
1. `pytest atlas/ml/test_training_data.py -v` — all pass
   - Test: builds correct feature matrix shape from mock DB data
   - Test: raises `InsufficientDataError` when < 200 samples
   - Test: joins `signal_history` and `trade_signals` on `signal_id` correctly
2. `pytest atlas/ml/test_meta_learner.py -v` — all pass
   - Test: trains on synthetic data, coefficients sum to ~1.0
   - Test: uses `TimeSeriesSplit` (verify no `train_test_split` or `KFold` anywhere)
   - Test: `predict` returns value in [0, 1] as native Python `float`
   - Test: model serialization round-trips via `msgspec` (no pickle)
3. `pytest pipeline/test_scorer.py -v` — existing tests still pass
   - Test: `meta_learner=None` → fixed weights (backward compatible)
   - Test: meta_learner provided → uses learned weights
   - Test: InsufficientDataError → scorer falls back to fixed weights, logs WARN once
4. `pyright --pythonversion 3.12 atlas/ml/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `import pickle` or `import joblib` anywhere
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `train_test_split` or `KFold` in meta_learner.py
- [ ] No bare `.fit()` or `.predict()` on the async path — must be `asyncio.to_thread()`
- [ ] No `np.float64` leaking into Pydantic models — cast to `float()`
- [ ] No `pandas` import — numpy/scipy only for ML math
- [ ] Fallback path exists for InsufficientDataError — does NOT crash the scorer
- [ ] All functions ≤ 40 lines
