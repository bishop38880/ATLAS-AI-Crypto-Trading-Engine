# SESSION 18 — Redis Streams: HYDRA Data Ingestion & Durability

## Context Files
@atlas/core/registry.py @atlas/shared/config.py @atlas/core/multi_asset_runner.py @atlas/providers/hydra/listener.py

## Prerequisites
Sessions 01–14 complete. HYDRA listener (Session 0) and provider infrastructure
must be operational. The listener currently uses Redis Pub/Sub — this session
migrates it to Streams.

## Goal
Migrate data ingestion from transient Pub/Sub to durable Redis Streams. Streams
give us consumer groups (multiple independent readers), persistence (no missed
liquidations during restarts), and backpressure. Keep Pub/Sub strictly for the
final signal fanout to PROMETHEUS.

**THE REDIS TOPOLOGY BOUNDARY:**
- **Ingestion (Streams):** HYDRA cascades, HYDRA price, DeFi Llama TVL —
  durable, consumer groups, XACK.
- **Egress (Pub/Sub):** `atlas:signals` to PROMETHEUS — transient,
  latency-critical, no persistence needed.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` →
   `msgspec`. `pandas`/`SQLAlchemy`/`pickle`/`joblib` → banned.
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru only.** No f-strings in loggers.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **Redis Topology.** Streams for ingestion, Pub/Sub for egress. NEVER mix.
9. **CoinGlass is removed.** Do NOT create streams for CoinGlass. Coinalyze is
   the derivatives provider.
10. **MAXLEN policy.** Use approximate trimming (`~ MAXLEN`) for performance.
    Exact trimming (`= MAXLEN`) is slow under load and is NOT the default here.
11. **Payload schemas are `msgspec.Struct`** — not bare dicts. Every stream has
    a declared Struct type so producers and consumers share a contract.

---

## Task 0 — Payload Schemas

Create `atlas/core/stream_payloads.py`:

```python
import msgspec
from decimal import Decimal
from datetime import datetime
from typing import Literal


class HydraCascadeStreamPayload(msgspec.Struct, frozen=True):
    """Durable shape of HYDRA cascade events in the ingestion stream."""
    event_id: str
    asset: str
    tier: Literal[1, 2, 3, 4]
    exchanges: list[str]
    total_liquidation_usd_str: str      # Decimal serialised as string
    timestamp_iso: str                  # ISO-8601 UTC

    def to_domain(self) -> "HydraCascadeEvent":
        """Convert to domain model (with Decimal hydration)."""
        from atlas.providers.hydra.listener import HydraCascadeEvent
        return HydraCascadeEvent(
            event_id=self.event_id,
            asset=self.asset,
            tier=self.tier,
            exchanges=self.exchanges,
            total_liquidation_usd=Decimal(self.total_liquidation_usd_str),
            timestamp=datetime.fromisoformat(self.timestamp_iso),
        )


class HydraPriceStreamPayload(msgspec.Struct, frozen=True):
    asset: str
    price_str: str                       # Decimal serialised
    source_exchange: str
    timestamp_iso: str


class DefillamaTvlStreamPayload(msgspec.Struct, frozen=True):
    protocol: str
    chain: str
    tvl_usd_str: str                     # Decimal serialised
    timestamp_iso: str
```

`Decimal` is serialised as a string in `msgspec` to preserve precision —
float is NOT acceptable for financial magnitudes.

## Task 1 — Stream Producer Layer

Create `atlas/core/stream_producer.py`:

```python
import msgspec
import redis.asyncio as redis_async
from loguru import logger


class StreamProducer:
    """XADD wrapper with approximate trimming."""

    STREAM_NAMES = {
        "hydra_cascades": "atlas:stream:hydra:cascades",
        "hydra_price": "atlas:stream:hydra:price",
        "defillama_tvl": "atlas:stream:defillama:tvl",
    }

    def __init__(self, redis_client: redis_async.Redis) -> None:
        self._redis = redis_client

    async def publish(
        self,
        stream_key: str,
        payload: msgspec.Struct,
        maxlen: int = 10_000,
    ) -> str:
        """Publish with approximate trimming (~ MAXLEN). Returns stream entry ID."""
        encoded = msgspec.json.encode(payload)
        entry_id = await self._redis.xadd(
            stream_key,
            {"data": encoded},
            maxlen=maxlen,
            approximate=True,              # ~ MAXLEN — fast trimming
        )
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id
```

## Task 2 — Stream Consumer Layer

Create `atlas/core/stream_consumer.py`:

```python
from dataclasses import dataclass
import msgspec
import redis.asyncio as redis_async
from redis.asyncio.client import Pipeline
from redis.exceptions import ResponseError

@dataclass(frozen=True)
class StreamMessage:
    stream: str
    entry_id: str
    payload_bytes: bytes


class StreamConsumer:
    def __init__(self, redis_client: redis_async.Redis) -> None:
        self._redis = redis_client

    async def setup_group(self, stream: str, group: str) -> None:
        """XGROUP CREATE, handle BusyGroupError idempotently."""
        try:
            await self._redis.xgroup_create(stream, group, id="$", mkstream=True)
       except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int = 100,
    ) -> list[StreamMessage]:
        """XREADGROUP with blocking, returns messages for caller-side XACK."""
        ...

    async def consume_latest(self, stream: str) -> StreamMessage | None:
        """XREVRANGE for agents that only need current state (no group semantics)."""
        ...

    async def ack(self, stream: str, group: str, entry_id: str) -> None:
        await self._redis.xack(stream, group, entry_id)
```

## Task 3 — Consumer Groups

Wire consumer groups in the orchestrator init:

- `scoring_group`: `MultiAssetRunner` reads HYDRA streams to trigger scoring cycles.
- `rag_group`: `RAGWriter` reads the same streams for historical context storage.
  Independent cursor — doesn't block scoring.

## Task 4 — HYDRA Listener Migration

**Critical migration task — remove the old Pub/Sub path.**

Update `atlas/providers/hydra/listener.py`:

1. The listener's **public API stays identical** — `get_latest_event`,
   `get_health_status`, `close` continue to work. Downstream agents do not change.
2. Internally, replace the Pub/Sub subscription loop with a consumer-group
   `XREADGROUP` loop on `atlas:stream:hydra:cascades` using
   group `hydra_listener_group`.
3. **DELETE the `_listen_loop` Pub/Sub subscription from Session 0.** Do not
   leave it running alongside the Streams consumer — the buffer would receive
   duplicates.
4. Heartbeat tracking continues to work (`time.monotonic()` on each message).
5. On startup: call `setup_group()` before `XREADGROUP`.

**Grep gate:** `grep -rn "pubsub\|psubscribe\|publish" atlas/providers/hydra/`
returns zero (except for `publish` inside the signal egress path, which is not
in `hydra/`).

### Producer-side

Whatever service originally published to the Pub/Sub channel
`hydra:cascades:live` must now push to the stream via `StreamProducer`. If the
HYDRA engine itself is producing these events, update its Redis client to use
`XADD` with an approximate-trim MAXLEN of 10,000.

### Egress Unchanged

The signal publisher (to PROMETHEUS) remains on `atlas:signals` Pub/Sub channel —
unchanged. Only ingestion migrates.

## Task 5 — Dead Letter and Replay

Create `atlas/core/stream_dlq.py`:

- Background task every 30 seconds.
- `async def process_dead_letters(stream: str, group: str) -> int`
  - `XAUTOCLAIM` messages pending longer than `DEAD_LETTER_THRESHOLD_SECONDS`
    (default: **300 seconds / 5 minutes** — not 60 seconds; legitimate long
    scoring cycles must not be wrongly declared dead).
  - Log dead letters with kwargs, acknowledge to clear pending list.
  - Return count processed.
- Threshold configurable via `PolarisSettings.dead_letter_threshold_seconds`.

## Quality Gates
1. `pytest atlas/core/test_stream_producer.py -v` — all pass.
2. `pytest atlas/core/test_stream_consumer.py -v` — all pass (XACK prevents re-delivery).
3. `pytest atlas/core/test_stream_dlq.py -v` — all pass.
4. `pytest atlas/providers/test_hydra_listener.py -v` — all pass post-migration.
5. `grep -rn "CoinGlass\|coinglass" atlas/core/stream_producer.py` — zero.
6. `grep -rn "psubscribe\|\.subscribe(" atlas/providers/hydra/` — zero (Pub/Sub removed).
7. `grep -rn "approximate=False\|approximate: False" atlas/core/stream_producer.py` — zero
   (approximate trimming enforced).
8. `pyright --pythonversion 3.12 atlas/core/` — zero errors.

## Anti-Pattern Checklist
- [ ] No Pub/Sub for ingestion — Streams only
- [ ] No Streams for egress — Pub/Sub only (`atlas:signals`)
- [ ] No CoinGlass streams — Coinalyze is the derivatives provider
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `import json` — `msgspec`
- [ ] No `os.getenv()` — `PolarisSettings`
- [ ] `BusyGroupError` handled in `XGROUP CREATE`
- [ ] MAXLEN trimming is approximate (`~`), not exact (`=`)
- [ ] Payloads are `msgspec.Struct` types, not bare dicts
- [ ] `Decimal` serialised as string (preserves precision)
- [ ] HYDRA listener Pub/Sub subscription removed — no dual-path ingestion
- [ ] Dead-letter threshold defaults to 300s (5 min), not 60s
- [ ] All functions ≤ 40 lines
