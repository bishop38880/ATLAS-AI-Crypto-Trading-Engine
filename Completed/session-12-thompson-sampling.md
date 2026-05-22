# SESSION 12 — Thompson Sampling: Bayesian Agent Weight Optimisation

## Context Files
@pipeline/scorer.py @atlas/ml/meta_learner.py @atlas/shared/config.py

## Prerequisites
Sessions 08–10 complete. Meta-learner and correlation tracker must be operational.
Session 11A (Post-Trade Learning) must be complete for outcome evaluation.

## Goal
Implement Thompson Sampling to dynamically explore-exploit agent weights. Each agent
gets a Beta distribution tracking its prediction accuracy. Weights are sampled from
these distributions each cycle, naturally allocating more weight to consistently
accurate agents over time.

**CRITICAL CONSTRAINT:** Per Context Document §18, agent conviction weights are NOT
modified autonomously in production. Thompson Sampling runs in **SHADOW MODE** — it
computes suggested weights, logs them, and stores them for human review. It does NOT
override the scorer's weights without explicit human approval via config flag
`THOMPSON_LIVE: bool = False`.

---

## NON-NEGOTIABLE INVARIANTS
1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru only.**
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **Scorer DAG position:** Thompson Sampling is Step 4 — shadow log only, NOT applied to score unless `THOMPSON_LIVE=True`.
9. **Atomic Redis updates.** The Beta distribution parameters (`alpha`, `beta`) MUST be updated atomically. Using two separate Redis keys with a non-atomic read-modify-write sequence creates a race where concurrent `update()` calls lose mutations. Use Redis hashes with `HINCRBYFLOAT` for native atomic increments.

---

## Task 1 — Beta Distribution Tracker (Atomic Redis Hash)

Create `atlas/ml/thompson_sampling.py`:

- Class `ThompsonSampler`
- Maintains `Beta(alpha, beta)` per agent in a **single Redis hash per agent** (via `redis.asyncio`)
  - Key: `thompson:{agent_name}` (one hash per agent)
  - Fields: `alpha`, `beta`, `last_decay_at`
  - Initialize all agents with `HSET thompson:{agent} alpha 1.0 beta 1.0` on first use (uniform prior)

**CRITICAL — Atomic increment pattern:**

```python
# CORRECT — atomic via HINCRBYFLOAT (Redis primitive, no race)
async def update(self, agent_name: str, was_correct: bool) -> None:
    key = f"thompson:{agent_name}"
    field = "alpha" if was_correct else "beta"
    new_value = await self._redis.hincrbyfloat(key, field, 1.0)
    logger.debug("thompson_updated", agent=agent_name, field=field, new=new_value)

# WRONG — two separate keys with read-modify-write race
#   alpha = await redis.get("thompson:agent:alpha")
#   alpha = float(alpha) + 1
#   await redis.set("thompson:agent:alpha", alpha)
# Two concurrent update() calls here lose one increment.
```

- `async def sample_weights(agent_names: list[str]) -> dict[str, float]`
  - For each agent: read the hash via `HGETALL`, sample a weight from `numpy.random.beta(alpha, beta)` — wrap in `asyncio.to_thread()` if sampling >10 agents (otherwise inline is fine)
  - Normalize the sampled weights so they sum to 1.0
  - All returned values are native Python `float`
- `async def get_expected_weights(agent_names: list[str]) -> dict[str, float]`
  - Read `(alpha, beta)` via `HGETALL`, compute expected value `alpha / (alpha + beta)` per agent
  - Normalize so weights sum to 1.0

## Task 2 — Non-Stationarity Decay

- `async def apply_decay(decay_factor: float = 0.995) -> None`
  - For each agent hash: read `(alpha, beta)`, multiply by decay, floor at 1.0, write back
  - Apply atomically via Lua script or `HMSET` transaction (`pipeline().hset(...).execute()`) so both fields update together
  - Run once per day (check `last_decay_at` field in the hash)
  - With 0.995 decay: 30d weight ~0.86, 90d ~0.64, 180d ~0.41

```python
async def apply_decay(self, agent_name: str, decay_factor: float = 0.995) -> None:
    key = f"thompson:{agent_name}"
    # Lua script guarantees atomic read-decay-write
    lua = """
    local alpha = tonumber(redis.call('HGET', KEYS[1], 'alpha') or '1.0')
    local beta = tonumber(redis.call('HGET', KEYS[1], 'beta') or '1.0')
    alpha = math.max(1.0, alpha * tonumber(ARGV[1]))
    beta = math.max(1.0, beta * tonumber(ARGV[1]))
    redis.call('HSET', KEYS[1], 'alpha', alpha, 'beta', beta, 'last_decay_at', ARGV[2])
    return {tostring(alpha), tostring(beta)}
    """
    await self._redis.eval(lua, 1, key, str(decay_factor), str(time.time()))
```

## Task 3 — Outcome Evaluation

Create `atlas/ml/outcome_evaluator.py`:

- Class `OutcomeEvaluator`
- Wired to Post-Trade Learning (Session 11A)
- Subscribes to Redis channel `atlas:learning_updates` (published by Session 11A)
- When an outcome arrives: determine which agents had directional bias matching the actual PnL sign
- `was_correct = (direction == "bullish" and pnl > 0) or (direction == "bearish" and pnl < 0)`
- Call `thompson_sampler.update()` for each agent (atomic HINCRBYFLOAT)

## Task 4 — Shadow Mode Logging

Update scorer (ConfluenceScorer or SignalSynthesiserAgent):
- After computing real score: also compute Thompson-suggested score
- Log both: `logger.info("thompson_shadow", real_score=87, thompson_score=82, thompson_weights={...})`
- Store in PostgreSQL (via `asyncpg`) for comparison analysis
- If `THOMPSON_LIVE=True`: use Thompson weights as dynamic override

## Quality Gates
1. `pytest atlas/ml/test_thompson_sampling.py -v` — all pass
   - Test: uniform prior `Beta(1,1)` → approximately equal weights
   - Test: 90% accuracy agent → highest weight
   - Test: decay reduces old observations' influence (alpha and beta both halve)
   - Test: decay never pushes alpha or beta below 1.0 floor
   - Test: all sampled weights sum to 1.0
   - Test: **100 concurrent `update()` calls on the same agent → final `alpha` equals initial + 100 (no lost updates — regression test for the race fixed by HINCRBYFLOAT)**
   - Test: hash is initialized with `alpha=1.0, beta=1.0` on first update to a new agent
2. `pytest atlas/ml/test_outcome_evaluator.py -v` — all pass
3. `pyright --pythonversion 3.12 atlas/ml/` — zero errors

## Anti-Pattern Checklist
- [ ] No `import aioredis` — use `redis.asyncio`
- [ ] No `import json` — use `msgspec`
- [ ] No `os.getenv()` — use `PolarisSettings`
- [ ] No `mypy` — use `pyright`
- [ ] Thompson NEVER modifies live scores unless `THOMPSON_LIVE=True`
- [ ] Alpha/Beta are stored in a single Redis HASH per agent, NOT separate keys
- [ ] Increments use `HINCRBYFLOAT` (atomic), NOT read-modify-write with `GET`/`SET`
- [ ] Decay uses a Lua script or `HMSET` transaction to update alpha+beta atomically
- [ ] All functions ≤ 40 lines
