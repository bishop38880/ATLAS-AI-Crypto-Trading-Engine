# SESSION 02 — Circuit Breakers + Provider Health Scoring

## Context Files
@atlas/shared/http_pool.py @atlas/core/registry.py @atlas/shared/config.py @agents/base.py

## Prerequisites
Session 01 complete. httpx pools and msgspec serialisation must be operational.

## Goal
Wrap every external provider call in a circuit breaker so that a failing
provider is automatically isolated after repeated failures, preventing
cascade failures across the scoring pipeline.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`. **No f-strings inside loggers** — use kwargs: `logger.info("event", provider=name, status=status)`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**

---

## Task 1 — Circuit Breaker Wrapper

Add `pybreaker` and `tenacity` to pyproject.toml. Create `atlas/core/circuit_breaker.py`:

- Class `ProviderCircuitBreaker`
- Wraps `pybreaker.CircuitBreaker` with Redis-backed state storage (via `redis.asyncio`)
- Constructor takes `provider_name: str`, `fail_max: int = 5`, `reset_timeout_seconds: int = 30`
- State transitions (CLOSED → OPEN → HALF_OPEN) are logged via Loguru with structured kwargs
- When circuit is OPEN, calls return empty data immediately without attempting the network call

Create a decorator `@with_circuit_breaker(provider_name)`:
```python
@with_circuit_breaker("coinalyze")
async def fetch_coinalyze_funding(self, asset: str) -> FundingData:
    ...
```

## Task 2 — Provider Health Score

Create `atlas/core/provider_health.py` with a `ProviderHealthTracker` class:

- Maintains a rolling window of the last 100 requests per provider
- Computes health score: `0.7 * success_rate + 0.3 * (1 - latency_penalty)`
- `latency_penalty` = fraction of requests exceeding 2x the provider's expected latency:
  - Coinalyze: 100ms
  - Pyth Hermes: 50ms
  - DeFi Llama: 200ms
  - Nansen MCP: 300ms
  - FRED: 500ms
  - CoinAPI MCP: 200ms
- Health score range: 0.0 (dead) to 1.0 (perfect)
- Scores stored in Redis (via `redis.asyncio`): key `provider:{name}:health_score`
- Serialize with `msgspec` (never stdlib `json`)

**NOTE:** CoinGlass has been removed from the stack. Do NOT include CoinGlass latency targets or health tracking. Coinalyze replaces it for derivatives data.

## Task 3 — Adaptive Timeouts

Add to `ProviderHealthTracker`:
- Track EMA (exponential moving average) of latency per provider, alpha=0.1
- Compute adaptive timeout: `timeout = ema_latency * 3.0`
- Clamp: floor 2.0 seconds, ceiling 30.0 seconds
- The httpx pool from Session 01 should read this adaptive timeout before each request

## Task 4 — Wire Into Registry

Update `core/registry.py`:
- Each registered provider gets a `ProviderCircuitBreaker` instance
- Each registered provider gets tracking via `ProviderHealthTracker`
- Add `get_provider_health(name: str) -> float` to registry
- Add `get_system_health() -> dict[str, float]` returning all provider scores

## Quality Gates
1. `pytest atlas/core/test_circuit_breaker.py -v` — all pass
   - Test: after 5 failures, circuit opens and returns empty immediately
   - Test: after reset_timeout, circuit moves to half-open
   - Test: successful call in half-open closes circuit
2. `pytest atlas/core/test_provider_health.py -v` — all pass
   - Test: 100% success rate → health = 1.0
   - Test: 50% success rate → health ≈ 0.35
   - Test: adaptive timeout clamps correctly
3. `pyright --pythonversion 3.12 atlas/core/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No f-strings in logger calls — use `logger.info("msg", key=val)`
- [ ] No CoinGlass references — Coinalyze is the derivatives provider
- [ ] No `print()` — use Loguru
- [ ] All functions ≤ 40 lines
