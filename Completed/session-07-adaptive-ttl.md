# SESSION 07 — Adaptive TTL + Stale-While-Revalidate Caching

## Context Files
@atlas/shared/config.py @atlas/core/registry.py @atlas/shared/http_pool.py @atlas/providers/hydra/listener.py

## Prerequisites
Sessions 01–06 complete. Sprint 1 test floor must not decrease.

## Goal
Replace fixed TTLs with volatility-adaptive TTLs that tighten during
high-volatility periods and relax during quiet markets. Add stale-while-
revalidate so agents always get data instantly while fresh data loads
in the background.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`. All env vars read through `PolarisSettings` from `atlas/settings.py`.
4. **40-line function limit.** Extract helpers for anything longer.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.** No orders, positions, credentials, or execution logic.
7. **Test floor is sacred.** `pytest` count must not decrease.
8. **Background tasks MUST be tracked.** Fire-and-forget `asyncio.create_task()` without a reference is a classic Python gotcha — the garbage collector may reap the task mid-execution, and exceptions are silently swallowed. Every `create_task()` call in this session must hold a strong reference in a set and attach a done-callback that logs exceptions and removes the reference.

---

## Task 1 — Volatility Monitor (HYDRA-Powered)

Create `atlas/core/volatility_monitor.py`:

- Class `VolatilityMonitor`
- Tracks real-time volatility ratio per asset:
  `vol_ratio = current_1h_volatility / 30d_average_volatility`
- `vol_ratio > 2.0` = high volatility, `vol_ratio < 0.5` = low volatility
- Method: `async def get_vol_ratio(asset: str) -> float`
- **HYDRA INTEGRATION:** Read volatility metrics directly from the local `HydraStreamListener` buffer. **Do NOT fetch OHLCV data externally.** No external HTTP calls to any exchange or aggregator for volatility data. The HYDRA buffer is the single source.
- Store computed ratio in Redis: `volatility:{asset}:ratio` (via `redis.asyncio`)
- Updated every 60 seconds from HYDRA buffer data

**CRITICAL NEGATIVE CONSTRAINT:** If you find yourself writing `httpx.get()` or any HTTP call to fetch OHLCV candles inside this module, STOP. That is a fatal architectural violation. HYDRA already has this data.

## Task 2 — Adaptive TTL Calculator

Create `atlas/core/adaptive_ttl.py`:

- Function `calculate_adaptive_ttl(base_ttl: int, vol_ratio: float) -> int`:
  ```python
  adaptive_ttl = base_ttl * (1.0 / vol_ratio)
  clamped_ttl = max(base_ttl * 0.25, min(base_ttl * 3.0, adaptive_ttl))
  return int(clamped_ttl)
  ```
- Base TTLs (current provider hierarchy):
  - Coinalyze (funding/OI): 10s
  - Pyth Hermes (price): 5s
  - HYDRA (liquidation): 5s
  - DeFi Llama: 300s
  - Nansen MCP: 300s
  - FRED: 3600s
- During 2x volatility: Coinalyze TTL drops from 10s → 5s
- During 0.5x volatility: Coinalyze TTL extends from 10s → 20s

**NOTE:** CoinGlass has been removed entirely from the POLARIS stack. Do NOT reference CoinGlass anywhere. Coinalyze is the derivatives data provider.

## Task 3 — Stale-While-Revalidate

Create `atlas/core/swr_cache.py`:

- Class `StaleWhileRevalidateCache`
- Inject `redis.asyncio.Redis` client (never `aioredis`)
- Two thresholds per cache entry:
  - `fresh_until`: adaptive TTL — data is fresh, return immediately
  - `stale_until`: `fresh_until * stale_factor` — data is stale but usable
  - `stale_factor`: 1.5 for price data, 3.0 for sentiment data
- Behaviour:
  - Request arrives, cache entry exists, `now < fresh_until` → return immediately
  - Request arrives, `fresh_until < now < stale_until` → return stale data immediately AND trigger background refresh (see below)
  - Request arrives, `now > stale_until` → cache miss, fetch synchronously

**CRITICAL — Background task tracking pattern:**

The background refresh must be tracked explicitly. The naive pattern is a fire-and-forget gotcha:

```python
# BROKEN — fire-and-forget, reference may be GC'd, exceptions are swallowed
asyncio.create_task(self._refresh(key, fetcher))
```

The correct pattern holds a reference and attaches a done-callback for cleanup and error logging:

```python
class StaleWhileRevalidateCache:
    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client
        self._background_tasks: set[asyncio.Task] = set()

    def _spawn_refresh(self, key: str, fetcher: Callable) -> None:
        """Kick off a background refresh and track the task."""
        task = asyncio.create_task(
            self._refresh(key, fetcher),
            name=f"swr_refresh:{key}",
        )
        # Hold a strong reference so GC doesn't reap the task mid-flight
        self._background_tasks.add(task)
        # Cleanup reference when done; log any exception that was raised
        task.add_done_callback(self._on_refresh_done)

    def _on_refresh_done(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                "swr_background_refresh_failed",
                task_name=task.get_name(),
                err=str(exc),
                exc_type=type(exc).__name__,
            )

    async def _refresh(self, key: str, fetcher: Callable) -> None:
        """Actual refresh work — all exceptions propagate to the done-callback."""
        new_value = await fetcher()
        await self._redis.set(key, msgspec.json.encode(new_value))
        # ... update fresh_until / stale_until
```

- Method: `async def get_or_fetch(key: str, fetcher: Callable, ttl: int, stale_factor: float) -> CacheResult`
- `CacheResult` Pydantic model (`frozen=True`): `data: Any`, `is_stale: bool`, `age_seconds: float`
- Serialize cache entries with `msgspec.json.encode` / `msgspec.json.decode` (never stdlib `json`)

## Task 4 — Wire Into Provider Layer

Update each provider's fetch method to use the SWR cache:
- Replace direct Redis get/set with `swr_cache.get_or_fetch()`
- TTL is computed via `calculate_adaptive_ttl()` using current vol_ratio
- Log when stale data is served (agent should see `is_stale` flag)
- All logging via Loguru with structured key-value format

## Quality Gates
1. `pytest atlas/core/test_adaptive_ttl.py -v` — all pass
   - Test: vol_ratio=1.0 → base TTL unchanged
   - Test: vol_ratio=2.0 → TTL halved
   - Test: vol_ratio=0.3 → TTL clamped at 3x base
   - Test: vol_ratio=10.0 → TTL clamped at 0.25x base (floor)
2. `pytest atlas/core/test_swr_cache.py -v` — all pass
   - Test: fresh data → immediate return, no background fetch
   - Test: stale data → immediate return + background fetch triggered
   - Test: expired data → synchronous fetch
   - Test: background task is tracked in `_background_tasks` set during execution
   - Test: background task reference is removed after completion
   - Test: background task exception is logged via the done-callback
   - Test: 100 concurrent stale reads do NOT spawn 100 background refreshes for the same key (deduplication — optional but recommended)
3. `pytest atlas/core/test_volatility_monitor.py -v` — all pass
   - Test: reads from HYDRA buffer, not HTTP
   - Test: stores result in Redis
4. `pyright --pythonversion 3.12 atlas/core/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` anywhere — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` calls — must use `PolarisSettings`
- [ ] No `print()` statements — must use `logger` from Loguru
- [ ] No HTTP calls in `volatility_monitor.py` — must read HYDRA buffer only
- [ ] No references to CoinGlass, Binance, or any removed provider
- [ ] No fire-and-forget `asyncio.create_task()` — tasks are tracked in a set and have done-callbacks
- [ ] All functions ≤ 40 lines
