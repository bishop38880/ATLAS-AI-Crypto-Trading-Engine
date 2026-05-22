# SESSION 0 — BaseProvider Scaffold & HYDRA Listener

## Run BEFORE any provider session and BEFORE Session 04.

## Context Files
@atlas/providers/__init__.py @atlas/providers/altfins/adapter.py @atlas/providers/coingecko/adapter.py @atlas/shared/config.py

## Goal
Establish `BaseProvider` — the abstract base class every external and internal
data provider inherits from. Build the `HydraStreamListener` for real-time local
liquidation cascade ingestion via Redis Pub/Sub.

**Sequencing note:** This session uses Redis Pub/Sub for HYDRA ingestion. Session 18
will migrate this listener to Redis Streams for durability. Plan accordingly — the
listener's public API (`get_latest_event`, `get_health_status`, `close`) must
remain stable across the migration so downstream agents don't need rewrites.

**Architecture note:** `BaseProvider` is the sanctioned provider-scaffolding
pattern as of the audit remediation. The previous ban on `BaseProvider`
inheritance in `050-tech-stack.mdc` has been lifted specifically because this
session is the canonical origin of the class.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib
   `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`,
   `sentence-transformers`, `pgvector`, `LlamaIndex`, `Pydantic.model_dump_json()`
   are all BANNED. Use `redis.asyncio` for Redis, `msgspec` for JSON, `httpx` for HTTP,
   `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru canonical patterns only.** `logger.info("msg | key={}", val)`
   (positional) or `logger.bind(key=val).info("msg")` (extras). NEVER
   `logger.info("msg", key=val)` with no `{key}` in the template — kwargs are
   silently discarded. NEVER f-strings.
6. **ATLAS has ZERO exchange awareness.** No CCXT, no Bitget client, no order management.
7. **Test floor is sacred.**
8. **HYDRA is a local service.** Do NOT use `httpx` or the HTTP semaphore for
   HYDRA. Do NOT hallucinate external HYDRA API endpoints. HYDRA speaks Redis
   Pub/Sub only in this session (migrated to Streams in Session 18).
9. **Financial fields use `Decimal`.** HYDRA `total_liquidation_usd` is
   `Decimal`, not `float`.
10. **Environment is Linux.** Paths use forward slashes. No Windows path literals.
11. **Tests live alongside code.** Place tests at
    `atlas/providers/test_base_provider.py` and
    `atlas/providers/hydra/test_listener.py`, never in a top-level `tests/`.

---

## Task 1 — BaseProvider ABC

Create `atlas/providers/base.py`:

```python
from abc import ABC, abstractmethod
from typing import Literal
import asyncio

import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, Field


class ProviderHealth(BaseModel, frozen=True):
    name: str
    status: Literal["HEALTHY", "DEGRADED", "OFFLINE"]
    last_update: float  # time.monotonic() timestamp
    error: str | None = None


class BaseProvider(ABC):
    """Abstract base class for all ATLAS data providers."""

    def __init__(
        self,
        provider_name: str,
        redis_client: redis_async.Redis,
        max_concurrent: int = 10,
    ) -> None:
        self._provider_name = provider_name
        self._redis = redis_client
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._status: Literal["HEALTHY", "DEGRADED", "OFFLINE"] = "HEALTHY"
        self._last_error: str | None = None

    def mark_degraded(self, error: str) -> None:
        self._status = "DEGRADED"
        self._last_error = error
        logger.warning(
            "provider degraded | provider={} | error={}",
            self._provider_name, error,
        )

    def mark_healthy(self) -> None:
        if self._status != "HEALTHY":
            logger.info("provider recovered | provider={}", self._provider_name)
        self._status = "HEALTHY"
        self._last_error = None

    @abstractmethod
    async def get_health_status(self) -> ProviderHealth: ...

    @abstractmethod
    async def close(self) -> None: ...
```

## Task 2 — The HYDRA Stream Listener

Create `atlas/providers/hydra/listener.py`:

```python
import asyncio
import time
from datetime import datetime
from decimal import Decimal
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel

from atlas.providers.base import BaseProvider, ProviderHealth


class HydraCascadeEvent(BaseModel, frozen=True):
    event_id: str
    asset: str
    tier: Literal[1, 2, 3, 4]          # strict — rejects 0, 5, etc.
    exchanges: list[str]
    total_liquidation_usd: Decimal
    timestamp: datetime


class HydraStreamListener(BaseProvider):
    """Local HYDRA cascade feed — Redis Pub/Sub (migrates to Streams in Session 18)."""

    CHANNEL = "hydra:cascades:live"
    HEARTBEAT_THRESHOLD_SECONDS = 2.0

    def __init__(self, redis_client: redis_async.Redis) -> None:
        super().__init__("hydra", redis_client, max_concurrent=1)
        self._buffer: dict[str, HydraCascadeEvent] = {}   # asset → latest event
        self._last_heartbeat: float = time.monotonic()
        self._listen_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self.CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                event = self._decode_event(message["data"])
                self._buffer[event.asset] = event
                self._last_heartbeat = time.monotonic()
                self.mark_healthy()
            except Exception as e:
                logger.error("hydra decode failed | error={}", str(e))
                self.mark_degraded(str(e))

    def _decode_event(self, raw: bytes) -> HydraCascadeEvent:
        parsed = msgspec.json.decode(raw)   # decode to dict
        return HydraCascadeEvent(**parsed)  # construct Pydantic

    def get_latest_event(self, asset: str | None = None) -> HydraCascadeEvent | None:
        """Read from in-memory buffer — no I/O."""
        if asset is not None:
            return self._buffer.get(asset)
        if not self._buffer:
            return None
        return max(self._buffer.values(), key=lambda e: e.timestamp)

    async def get_health_status(self) -> ProviderHealth:
        delta = time.monotonic() - self._last_heartbeat
        status: Literal["HEALTHY", "DEGRADED", "OFFLINE"] = (
            "DEGRADED" if delta > self.HEARTBEAT_THRESHOLD_SECONDS else "HEALTHY"
        )
        return ProviderHealth(
            name="hydra",
            status=status,
            last_update=self._last_heartbeat,
            error=self._last_error,
        )

    async def close(self) -> None:
        if self._listen_task is not None:
            self._listen_task.cancel()
```

**Heartbeat discipline:** `get_health_status()` is passive — it reads
`time.monotonic() - self._last_heartbeat` when called. No background
`asyncio.sleep(2)` loop monitoring heartbeats. This is the canonical pattern.

**msgspec + Pydantic decode pattern:** `msgspec.json.decode(raw)` returns a
plain `dict`. `HydraCascadeEvent(**parsed)` constructs the Pydantic model from
that dict. Do NOT try `msgspec.json.decode(raw, type=HydraCascadeEvent)` —
msgspec's `type=` parameter does not support Pydantic `BaseModel`.

## Task 3 — Retrofit Existing Providers

Update `altFINs` and `CoinGecko` adapters to inherit from `BaseProvider`:

- Remove any local `mark_degraded()` / `mark_healthy()` methods (now inherited).
- Do NOT change public method signatures.
- **Purge ALL f-strings AND un-referenced trailing kwargs from logger calls.**
  Replace with positional `{}` format:

```python
# WRONG — f-string (evaluates eagerly, bypasses Loguru)
logger.info(f"Fetched {count} items from {provider}")

# WRONG — un-referenced kwargs (silently discarded — no value ends up in output)
logger.info("Fetched items", count=count, provider=provider)

# CORRECT — positional format substitution
logger.info("Fetched items | count={} | provider={}", count, provider)

# ALSO CORRECT — bind() for structured record.extra (requires JSON sink)
logger.bind(count=count, provider=provider).info("Fetched items")
```

## Quality Gates
1. `pytest atlas/providers/hydra/test_listener.py -v` — all pass.
   - Mock Redis Pub/Sub publish → listener decodes via `msgspec`, updates buffer.
   - `get_latest_event()` returns most recent event from buffer.
   - No heartbeat for 2.1s → `get_health_status()` returns DEGRADED.
   - Heartbeat within 2.0s → `get_health_status()` returns HEALTHY.
   - `tier=0` or `tier=5` raises `ValidationError`.
   - `total_liquidation_usd` is `Decimal`, not `float`.
2. `pytest atlas/providers/test_base_provider.py -v` — all pass.
   - Semaphore limits concurrency.
   - `mark_degraded` / `mark_healthy` state transitions.
3. `grep -rn "import requests\|^import json\|^from json\|response\.json()" atlas/providers/ --include="*.py"` — zero.
4. `grep -rEn 'logger\.(info|error|warning|debug|critical).*f["'"'"']' atlas/providers/` — zero (no f-strings).
5. `grep -rEn 'logger\.(info|error|warning|debug|critical)\([^,"]*,\s*\w+=' atlas/providers/ --include="*.py"` — zero (no un-referenced trailing kwargs).
6. `pyright --pythonversion 3.12 atlas/providers/` — zero errors.
7. `ls tests/test_base_provider.py tests/test_hydra_listener.py 2>&1 | grep "No such"` — both absent (tests alongside code).

## Anti-Pattern Checklist
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `import json` — `msgspec`
- [ ] No `response.json()` — `msgspec.json.decode(response.content)` → dict → Pydantic
- [ ] No `model_dump_json()` — `msgspec.json.encode(model.model_dump())`
- [ ] No `import requests` — `httpx`
- [ ] No `os.getenv()` — `PolarisSettings`
- [ ] No f-strings in logger calls
- [ ] No un-referenced trailing kwargs in logger calls (silent data loss)
- [ ] No `print()` — Loguru
- [ ] No external HTTP calls in HYDRA listener — Redis Pub/Sub only
- [ ] No background `asyncio.sleep` loop for heartbeat — passive `time.monotonic()` check
- [ ] `HydraCascadeEvent.tier` is `Literal[1, 2, 3, 4]`, not bare `int`
- [ ] `HydraCascadeEvent.total_liquidation_usd` is `Decimal`, not `float`
- [ ] Listener public API is stable — Session 18 will migrate Pub/Sub → Streams
      without changing method signatures
- [ ] All functions ≤ 40 lines
- [ ] Tests at `atlas/providers/test_base_provider.py` and
      `atlas/providers/hydra/test_listener.py`, never `tests/test_*`
