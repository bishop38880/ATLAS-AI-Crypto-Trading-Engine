# SESSION 03 — Validation Gate Upgrade: Anomaly Detection + Adaptive Staleness

## Context Files
@atlas/core/validation_gate.py @atlas/core/registry.py @atlas/shared/config.py @agents/base.py

## Prerequisites
Sessions 01–02 complete. Circuit breakers and provider health scoring must be operational.

## Goal
Upgrade the Validation Gate from basic Z-score anomaly detection to a four-layer
pipeline: Pydantic v2 schema validation → Isolation Forest anomaly detection →
cross-source consistency → adaptive staleness detection.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **THE ASYNC ML BOUNDARY:** `sklearn.ensemble.IsolationForest` is synchronous and CPU-bound. `.fit()` and `.predict()` calls **MUST** be wrapped in `await asyncio.to_thread(...)` when called from async context. This is critical — blocking the uvloop thread with sklearn will destroy latency for all concurrent operations.

---

## Task 1 — Isolation Forest Anomaly Detector

Create `atlas/core/anomaly_detector.py`:

- Class `AnomalyDetector` using `sklearn.ensemble.IsolationForest`
- Maintains a rolling buffer of the last 200 data points per (provider, asset, metric) triple, stored in Redis (via `redis.asyncio`) as a list
- Serialize buffer entries with `msgspec` (never stdlib `json`)
- On each new data point: append to buffer, retrain if buffer size is a multiple of 50 (avoid retraining every tick)
- `contamination=0.05` (expect 5% outliers)
- Method: `async def check_anomaly(provider: str, asset: str, metric: str, value: float) -> AnomalyResult`
- `AnomalyResult` Pydantic model (`frozen=True`):
  `is_anomaly: bool`, `anomaly_score: float`, `z_score: float`, `rolling_mean: float`, `rolling_std: float`
- Fallback: if buffer has <30 data points, fall back to Z-score only (|Z| > 4.0 = anomaly)

**ASYNC BOUNDARY ENFORCEMENT:**
```python
# CORRECT — sklearn wrapped in to_thread
await asyncio.to_thread(self._forest.fit, buffer_array)
score = await asyncio.to_thread(self._forest.decision_function, [[value]])

# FATAL ERROR — blocks uvloop
self._forest.fit(buffer_array)  # NEVER IN ASYNC CONTEXT
```

- Cast all `np.float64` outputs to native `float` before returning in the Pydantic model

## Task 2 — Cross-Source Consistency Checker

Create `atlas/core/consistency_checker.py`:

- Class `ConsistencyChecker`
- Knows which providers report overlapping metrics:
  - BTC spot price: Pyth Hermes + Coinalyze
  - Open interest: Coinalyze + OKX MCP
  - Funding rate: Coinalyze + OKX MCP
- Method: `async def check_consistency(metric: str, values: dict[str, float]) -> ConsistencyResult`
- `ConsistencyResult` (`frozen=True`): `is_consistent: bool`, `max_divergence_pct: float`, `divergent_providers: list[str]`
- Thresholds: >0.5% divergence on spot price = warning, >2% divergence on open interest = warning

**NOTE:** CoinGlass has been removed from the stack. Do NOT reference CoinGlass as a consistency source. Use Coinalyze, Pyth Hermes, and OKX MCP as overlap sources.

## Task 3 — Adaptive Staleness Detection

Update the staleness check in the Validation Gate:

- Instead of fixed `2x TTL` threshold, compute adaptive staleness:
  `stale_threshold = 3 * P90_update_interval`
- Track P90 update interval per provider in Redis (via `redis.asyncio`, rolling 100 updates)
- If a provider normally updates every 5 seconds but hasn't updated in 15 seconds, that's stale — even if its TTL says 60 seconds

## Task 4 — Wire Into Validation Gate Pipeline

Update `atlas/core/validation_gate.py` to run the four checks in sequence:
1. Schema validation (existing Pydantic check — keep as-is)
2. Staleness check (upgraded to adaptive)
3. Anomaly detection (new Isolation Forest — wrapped in `asyncio.to_thread`)
4. Cross-source consistency (new)

Each check appends to a `validation_flags: list[str]` on the data payload.
Schema or staleness failure → reject data, return empty, mark DEGRADED.
Anomaly or consistency flags → pass data through WITH flags attached.
Agents receiving flagged data must acknowledge the flags in their reasoning.

## Quality Gates
1. `pytest atlas/core/test_anomaly_detector.py -v` — all pass
   - Test: normal values pass, extreme outlier flagged
   - Test: fallback to Z-score when buffer < 30
   - Test: sklearn calls wrapped in `asyncio.to_thread` (mock and verify)
   - Test: returned values are native `float`, not `np.float64`
2. `pytest atlas/core/test_consistency_checker.py -v` — all pass
   - Test: matching prices → consistent
   - Test: 1% price divergence → warning
3. `pytest atlas/core/test_validation_gate.py -v` — all pass
   - Test: full pipeline runs all 4 checks in order
   - Test: schema failure short-circuits remaining checks
4. `pyright --pythonversion 3.12 atlas/core/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No bare `.fit()` or `.predict()` on async path — must be `asyncio.to_thread()`
- [ ] No `np.float64` in Pydantic models — cast to `float()`
- [ ] No CoinGlass references — use Coinalyze/Pyth/OKX as overlap sources
- [ ] No `print()` — use Loguru
- [ ] All functions ≤ 40 lines
