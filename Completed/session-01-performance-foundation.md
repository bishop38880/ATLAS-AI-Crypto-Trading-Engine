# SESSION 01 — Performance Foundation: uvloop + httpx Pooling + msgspec

## Context Files
@atlas/shared/config.py @atlas/core/registry.py @agents/base.py @pipeline/orchestrator.py @pyproject.toml

## Prerequisites
Session 00 complete. Signal schema v2 must be in place.

## Goal
Install and wire three performance primitives that the entire ATLAS codebase
will build on. After this session, every I/O path uses connection pooling,
the event loop is 2-4x faster, and JSON serialisation is 6-8x faster.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**

---

## Task 1 — uvloop Event Loop

Add `uvloop` to pyproject.toml dependencies. Create `atlas/shared/loop.py`:

```python
"""Event loop configuration — install uvloop at import time."""
import uvloop
uvloop.install()
```

Import this module at the TOP of `atlas/__init__.py` and `backend/main.py`
(or wherever the FastAPI app is created) so uvloop is active before any
asyncio code runs. Add a smoke test that verifies `asyncio.get_event_loop_policy()`
returns a uvloop policy.

## Task 2 — httpx Connection Pool Manager

Create `atlas/shared/http_pool.py` with a `ProviderHttpPool` class:

- Constructor takes `base_url: str`, `timeout: float = 10.0`,
  `max_connections: int = 20`, `max_keepalive: int = 10`
- Creates ONE `httpx.AsyncClient` with `http2=True` and these limits
- Exposes `async def get(path: str, params: dict | None) -> httpx.Response`
- Exposes `async def close() -> None` for graceful shutdown
- Has `async def __aenter__` / `__aexit__` for context manager usage

Register pool instances in `core/registry.py` — one pool per provider.
Current active providers requiring HTTP pools:
- Coinalyze (funding rates, OI)
- Pyth Hermes (price feeds, SSE streaming)
- DeFi Llama (TVL, yield data)
- Nansen MCP
- FRED (macro data)
- CoinAPI MCP

**NOTE:** HYDRA uses Redis Pub/Sub, not HTTP — it does NOT get an HTTP pool.
CoinGlass has been removed from the stack entirely — do NOT create a pool for it.

## Task 3 — msgspec Serialisation

Add `msgspec` to pyproject.toml. Create `atlas/shared/serialisation.py`:

- `encode_json(obj: Any) -> bytes` — wraps `msgspec.json.encode`
- `decode_json(data: bytes, type: type[T]) -> T` — wraps `msgspec.json.decode`
- For Pydantic models, add a helper `pydantic_to_msgspec(model: BaseModel) -> bytes`

Find **ALL** existing `json.loads` / `json.dumps` in the codebase and replace
them with the msgspec wrappers. List every file you changed.

**CRITICAL:** After this session, `import json` must not appear anywhere in the
ATLAS codebase except in test files that explicitly test stdlib compatibility.

## Quality Gates
1. `pytest atlas/shared/test_http_pool.py -v` — all pass
   - Test: pool reuses connections (mock transport)
   - Test: timeout raises and returns gracefully
   - Test: close() actually closes the client
2. `pytest atlas/shared/test_loop.py -v` — uvloop confirmed active
3. `grep -r "json.loads\|json.dumps" atlas/ --include="*.py"` — returns zero matches
4. `grep -r "import json" atlas/ --include="*.py" | grep -v test_` — returns zero matches
5. `pyright --pythonversion 3.12 atlas/shared/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import json` in non-test files
- [ ] No `import requests` anywhere
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `print()` — use Loguru
- [ ] No CoinGlass pool registration
- [ ] No `aioredis` — use `redis.asyncio`
- [ ] All functions ≤ 40 lines
