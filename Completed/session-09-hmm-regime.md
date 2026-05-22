# SESSION 09 — HMM Regime Detection with Regime-Adaptive Weight Profiles

## Context Files
@agents/regime/ @pipeline/scorer.py @atlas/shared/config.py @agents/base.py

## Prerequisites
Sessions 01–08 complete. The ConfluenceScorer must support the meta-learner fallback path (Session 08) before regime-adaptive weights are layered on top.

## Goal
Add a Hidden Markov Model that classifies the current market into one of
three regimes (bull/bear/volatile). Each regime has its own weight profile
for agent scoring. The final conviction score blends regime-weighted
scores by regime probability, replacing a single static weight set.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **THE ASYNC ML BOUNDARY:** `hmmlearn` is synchronous and CPU-bound. You **MUST NEVER** execute `.fit()`, `.predict()`, `.predict_proba()`, `.score()`, or any hmmlearn method directly on the async event loop. **ALL** hmmlearn calls must be wrapped in `await asyncio.to_thread(...)`. This also applies to any numpy matrix operations that are non-trivial (>1ms).
9. **Numpy/Scipy exception:** ML modules may use `numpy` and `scipy`. All `np.float64` outputs **must be cast to `float()`** before exiting the module. No numpy types in Pydantic models or Redis.
10. **Polars for dataframes, numpy for ML math.** Polars IS approved in the POLARIS tech stack (see updated `050-tech-stack.mdc`). Use Polars `LazyFrame` for data loading and transformations. Use numpy arrays for the HMM feature matrix because `hmmlearn` expects numpy. **Pandas is banned.** If you see any reference suggesting pandas or "numpy-only", ignore it — Polars is the sanctioned dataframe library.

---

## Task 1 — HMM Regime Detector

Create `atlas/ml/regime_detector.py`:

- Class `HMMRegimeDetector`
- Uses `hmmlearn.GaussianHMM` with `n_components=3`, `covariance_type="full"`
- Input features (numpy array): Log returns, Realised volatility (20-period), Volume ratio
- Training: fit on last 90 days of 4H candles per asset
  - Data source: query from PostgreSQL via `asyncpg` or read from Redis cache (load into Polars `LazyFrame` for transformations, convert to numpy at the HMM boundary)
  - **Training must be wrapped:** `await asyncio.to_thread(self._hmm.fit, features)`
- Method: `def detect_regime(features: np.ndarray) -> RegimeResult`
  - This is called via `await asyncio.to_thread(detector.detect_regime, features)` from the async layer
- `RegimeResult` Pydantic model (`frozen=True`):
  - `current_regime: str` — one of `"bull"`, `"bear"`, `"volatile"`
  - `regime_probabilities: dict[str, float]` — all values native `float`, not `np.float64`
  - `regime_duration_bars: int`
  - `transition_probability: float` — native `float`
- Add `hmmlearn` to `pyproject.toml` dependencies

**ASYNC BOUNDARY ENFORCEMENT:**
```python
# CORRECT
regime_result = await asyncio.to_thread(detector.detect_regime, features)
await asyncio.to_thread(detector.fit, training_data)

# FATAL ERROR — blocks uvloop
regime_result = detector.detect_regime(features)  # NEVER IN ASYNC CONTEXT
```

## Task 2 — Regime-Specific Weight Profiles

Create `atlas/ml/regime_weights.py`:

- Define `REGIME_WEIGHTS: dict[str, dict[str, float]]` mapping regime → category multipliers
- Three profiles:

| Category     | Bull | Bear | Volatile |
|-------------|------|------|----------|
| TECHNICAL   | 1.2  | 0.8  | 0.6      |
| DERIVATIVES | 0.8  | 1.3  | 1.4      |
| ONCHAIN     | 1.0  | 1.0  | 0.7      |
| SENTIMENT   | 1.1  | 0.9  | 0.5      |
| WHALE       | 0.9  | 1.2  | 1.3      |
| LIQUIDATION | 0.7  | 1.3  | 1.5      |
| REGIME      | 1.0  | 1.0  | 1.0      |
| FUNDING     | 0.8  | 1.2  | 1.1      |
| NEWS_MACRO  | 1.0  | 1.0  | 1.0      |
| CORRELATION | 1.0  | 1.0  | 1.0      |

Note: RISK is NOT in this table. Risk is veto-only (see Session 04A) with `max_points=0` — it doesn't participate in regime-weighted scoring because it doesn't contribute to the confluence score at all.

- Function: `def get_regime_adjusted_weights(base_weights: dict[str, float], regime_result: RegimeResult) -> dict[str, float]`
  - Blends weights by regime probability: `sum(prob * base_weight * multiplier for regime, prob in regime_probabilities)`
  - Normalizes so weights sum to 1.0
  - All returned values must be native Python `float`

## Task 3 — Blended Scoring

Update `ConfluenceScorer.score()`:

- Accept `regime_result: RegimeResult | None` parameter
- **This applies in the FALLBACK path only** (when meta-learner is not trained):
  - Compute blended score using regime-weighted agent scores
  - `blended = sum(regime_prob * calculate_weighted_score(agent_results, REGIME_WEIGHTS[regime]) for regime, regime_prob in regime_result.regime_probabilities.items())`
  - Fall back to uniform weights if regime data unavailable
- **When meta-learner IS trained:** Regime weights are NOT applied to the meta-learner output. The meta-learner has already learned regime-aware patterns from historical data. Regime result is logged for observability only.
- **Scorer DAG position:** Regime weights sit at Step 2 (fallback path), after the meta-learner check (Step 1) and before decorrelation (Step 2b, Session 10).

## Task 4 — Integration with Market Regime Agent

Update the existing Market Regime agent to use HMM output:
- Agent includes regime classification in its explanations
- Passes `RegimeResult` up to orchestrator via the agent result
- HMM detection call wrapped in `asyncio.to_thread()` inside the agent
- Agent reads OHLCV history from the HYDRA buffer or Redis cache — no external HTTP calls for candle data
- Data transformations use Polars `LazyFrame`; convert to numpy at the HMM call boundary

## Quality Gates
1. `pytest atlas/ml/test_regime_detector.py -v` — all pass
   - Test: 3-regime detection on synthetic data produces valid `RegimeResult`
   - Test: `regime_probabilities` values sum to ~1.0
   - Test: all numeric fields are native Python `float` (not `np.float64`)
2. `pytest atlas/ml/test_regime_weights.py -v` — all pass
   - Test: bull regime upweights TECHNICAL
   - Test: volatile regime upweights DERIVATIVES and LIQUIDATION
   - Test: blended weights sum to 1.0
3. `pytest pipeline/test_scorer.py -v` — existing tests still pass
   - Test: `regime_result=None` → uniform fallback weights
   - Test: regime blending produces different scores than static weights
4. `pyright --pythonversion 3.12 atlas/ml/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `import pandas` — use Polars for dataframes, numpy for ML math
- [ ] No `import pickle` or `import joblib`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No bare `.fit()` or `.predict()` on async path — must be `asyncio.to_thread()`
- [ ] No `np.float64` in Pydantic models — cast to `float()`
- [ ] No `mypy` references — use `pyright`
- [ ] All functions ≤ 40 lines
