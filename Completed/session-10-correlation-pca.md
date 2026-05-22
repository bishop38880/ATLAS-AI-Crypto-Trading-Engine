# SESSION 10 — Correlation Monitor + PCA Decorrelation

## Context Files
@pipeline/scorer.py @agents/base.py @atlas/shared/config.py

## Prerequisites
Sessions 01–09B complete. All 10 scoring agents must be registered and the ConfluenceScorer must support regime-adaptive weights (Session 09) and conviction suppression (Session 09B).

## Goal
When two agents produce highly correlated signals (ρ > 0.7), their combined
weight is inflated — they're counting the same information twice. Add a
correlation monitor that detects this and a decorrelation step that
down-weights redundant signals.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `RRF`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers`, `pgvector` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL, Qdrant + LanceDB for vector search.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **THE ASYNC ML BOUNDARY:** `scipy` and `numpy` correlation/PCA operations are synchronous and CPU-bound. Any non-trivial matrix computation (correlation matrix, PCA fit/transform on >50 rows) **MUST** be wrapped in `await asyncio.to_thread(...)`. Small vector operations (dot products, single-row normalization) are permitted inline.
9. **Numpy/Scipy exception:** ML modules may use `numpy` and `scipy` for correlation and PCA math. `pandas` is **BANNED** — even though it has `.corr()`, use `numpy.corrcoef()` or `scipy.stats.pearsonr()` directly. All `np.float64` outputs **must be cast to `float()`** before exiting the module.

---

## Task 1 — Agent Correlation Tracker

Create `atlas/ml/correlation_tracker.py`:

- Class `AgentCorrelationTracker`
- Maintains a rolling window (last 200 cycles) of all agent scores in Redis (via `redis.asyncio`)
- On each cycle, stores: `correlation:scores:{cycle_id}` → `msgspec`-encoded dict `{agent_name: score, ...}`
- Method: `async def record_scores(cycle_id: str, scores: dict[str, float]) -> None`
- Method: `async def compute_correlation_matrix() -> np.ndarray`
  - Retrieves last 200 cycles from Redis
  - Uses `numpy.corrcoef()` (NOT `pandas.DataFrame.corr()`)
  - **Wrap in `asyncio.to_thread()`** since this involves matrix operations on 200×10 data
  - Returns 10×10 correlation matrix
- Method: `async def get_correlated_pairs(threshold: float = 0.7) -> list[tuple[str, str, float]]`
  - Returns pairs of agents with |ρ| > threshold
  - All ρ values cast to native `float`

## Task 2 — Correlation-Aware Weight Adjustment

Create `atlas/ml/decorrelation.py`:

- Function: `def calculate_decorrelated_weights(raw_weights: dict[str, float], correlation_matrix: np.ndarray, agent_names: list[str]) -> dict[str, float]`
- For each pair with |ρ| > 0.7:
  - Effective combined weight = `w_i + w_j * (1 - |ρ|)`
  - Split between the two agents proportionally to their individual weights
- This ensures perfectly correlated agents effectively share one agent's weight instead of double-counting
- All returned weights must be native Python `float`, not `np.float64`
- Weights must remain positive after adjustment
- Weights must be re-normalized to sum to 1.0

**NOTE:** This function is pure math (no I/O), but if called with large matrices, wrap the caller in `asyncio.to_thread()`.

## Task 3 — PCA Dimensionality Check (Diagnostic Only)

Add PCA analysis as a diagnostic — **NOT used in scoring directly, only logged**:

- Create `atlas/ml/pca_diagnostic.py`
- Function: `def run_pca_diagnostic(score_history: np.ndarray, agent_names: list[str]) -> PCADiagnosticResult`
- Uses `sklearn.decomposition.PCA` — **wrap in `asyncio.to_thread()` at call site**
- Run PCA on the rolling window of agent scores
- Log: number of components explaining 95% of variance
- Expected: 4–6 components for 10 agents (if fewer, agents are redundant)
- If components < 4: `logger.warning("agent_diversity_critically_low", n_components=n_components, total_agents=10)`
- `PCADiagnosticResult` Pydantic model (`frozen=True`):
  - `n_components_95pct: int`
  - `explained_variance_ratios: list[float]` — native `float`, not `np.float64`
  - `is_diversity_low: bool`
- Store PCA results in PostgreSQL (via `asyncpg`) for trend tracking:
  ```sql
  INSERT INTO pca_diagnostics (timestamp, n_components, variance_ratios, diversity_low)
  VALUES ($1, $2, $3, $4)
  ```

**CRITICAL:** PCA output is for LOGGING and ALERTING only. It does NOT modify the scoring pipeline. If you find yourself wiring PCA output into `ConfluenceScorer.score()`, STOP — that is not the design.

## Task 4 — Wire Into Scorer

Update `ConfluenceScorer.score()`:

- Before applying weights, call `await asyncio.to_thread(calculate_decorrelated_weights, ...)`
- Pass decorrelated weights instead of raw weights to the scoring formula
- Log: which pairs were correlated, adjustment magnitude
- **Scorer DAG position — STACKING ORDER:**
  1. **Step 1 (Session 08):** If Meta-Learner trained → use its prediction as base score. SKIP Steps 2a/2b.
  2. **Step 2a (Session 09):** If no Meta-Learner → compute HMM regime-adaptive weights
  3. **Step 2b (THIS SESSION):** Apply decorrelation adjustment to regime weights → `final_weights`
  4. **Step 2c:** Compute weighted sum using `final_weights` → base score
  5. **Step 3 (Session 09B):** Subtract `conviction_suppression` from base score. Floor at 0.
  6. **Step 4 (Session 12):** Thompson Sampling — shadow log only (not applied unless `THOMPSON_LIVE=True`)

This means decorrelation is applied to the FALLBACK path only (when meta-learner is not trained). When the meta-learner IS trained, decorrelation is logged for diagnostics but not applied to the score.

## Quality Gates
1. `pytest atlas/ml/test_correlation_tracker.py -v` — all pass
   - Test: perfectly correlated agents detected with ρ ≈ 1.0
   - Test: independent agents show ρ ≈ 0.0
   - Test: stores/retrieves scores from Redis correctly
   - Test: returned ρ values are native `float`
2. `pytest atlas/ml/test_decorrelation.py -v` — all pass
   - Test: correlated pair has reduced combined weight
   - Test: uncorrelated agents keep original weights
   - Test: all weights remain positive after adjustment
   - Test: weights sum to 1.0 after adjustment
3. `pytest atlas/ml/test_pca_diagnostic.py -v` — all pass
   - Test: 10 identical signals → 1 component at 95%
   - Test: 10 independent signals → ~10 components at 95%
   - Test: diversity warning triggered when components < 4
4. `pytest pipeline/test_scorer.py -v` — existing tests still pass
   - Test: decorrelation applied in fallback path
   - Test: decorrelation NOT applied when meta-learner is active
5. `pyright --pythonversion 3.12 atlas/ml/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `import pandas` — use `numpy.corrcoef()` for correlation
- [ ] No `import pickle` or `import joblib`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No bare `PCA().fit()` on async path — must be `asyncio.to_thread()`
- [ ] No `np.float64` in Pydantic models — cast to `float()`
- [ ] No `pd.DataFrame` anywhere — numpy arrays only for ML math
- [ ] No `mypy` references — use `pyright`
- [ ] PCA output is diagnostic only — NOT wired into scoring
- [ ] All functions ≤ 40 lines
