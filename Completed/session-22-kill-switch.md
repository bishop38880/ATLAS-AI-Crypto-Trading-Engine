# SESSION 22 — Emergency Kill Switch (Standalone PROMETHEUS Circuit Breaker)

## Context Files
@prometheus/ @schema.sql @atlas/shared/config.py

## Prerequisites
Session 21 (optional enhancement sessions) — none. Kill switch is standalone and
must function even when ATLAS is down. Can be deployed independently.

## Goal
Build the emergency kill-switch system — a last-line-of-defense circuit breaker
that can halt trading independently of ATLAS, PROMETHEUS agents, and the main
event loop. It must work when nothing else does.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` →
   `msgspec`. `pandas`/`SQLAlchemy`/`pickle`/`joblib` → banned.
3. **`PolarisSettings` only — WITH ONE DOCUMENTED EXCEPTION.** The kill switch
   may read `os.environ` for the **panic key ONLY**, because a failure of
   `PolarisSettings` itself (e.g., corrupted `.env`) must not disable the emergency
   halt. Any other settings access uses `PolarisSettings`.
4. **40-line function limit.**
5. **Loguru only.** No f-strings in loggers.
6. **Decimal for financial math.**
7. **Test floor is sacred.**
8. **Standalone invariant.** Kill switch module has NO dependency on ATLAS. It
   imports from `prometheus/kill_switch/` only.
9. **CRITICAL — HMAC comparison MUST use `hmac.compare_digest()`**, not `==`.
   Panic key verification with `==` is vulnerable to timing attacks. An automatic
   audit failure.
10. **HALT is separate from RESUME.** Two Redis keys: `trading_halted`
    (persists until explicitly cleared) and `resume_trigger` (one-shot). Do
    not collapse into a single toggle — explicit resume must require a fresh
    authenticated command.
11. **SYSTEM_HALT channel is canonical.** All halt publications use Redis Pub/Sub
    channel `prometheus:system_halt` with msgspec-encoded `SystemHaltEvent`.
    Reconciler from Session 14 consumes this same channel.
12. **File I/O without `aiofiles`.** `aiofiles` is **NOT APPROVED** for this stack.
    All disk writes use `await asyncio.to_thread(Path.write_bytes, ...)` or
    `Path.write_text(...)`. This preserves async semantics without adding a
    dependency.

---

## Task 1 — Kill Switch Schema

### `SystemHaltEvent` (published to `prometheus:system_halt`)

```python
import msgspec
from typing import Literal


class SystemHaltEvent(msgspec.Struct, frozen=True):
    event_type: Literal["HALT", "RESUME"]
    reason: Literal[
        "MANUAL_PANIC_KEY",
        "SPIKE_DETECTOR",
        "POSITION_LIMIT_BREACH",
        "API_FAILURE_STORM",
        "RECONCILER_MISMATCH",
        "LIQUIDATION_PROXIMITY",
        "MANUAL_RESUME",
    ]
    triggered_by: str
    timestamp_iso: str                   # ISO-8601 UTC
    details: dict[str, str | int | float]
```

### Database Schema

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL CHECK (event_type IN ('HALT', 'RESUME')),
    reason TEXT NOT NULL,
    triggered_by TEXT NOT NULL,
    details JSONB NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_kill_switch_triggered_at
    ON kill_switch_events (triggered_at DESC);
```

## Task 2 — Kill Switch Core

Create `prometheus/kill_switch/core.py`:

```python
"""Emergency kill switch — standalone, documented scoped override."""

import hmac
import os
from datetime import datetime
from pathlib import Path
from typing import Literal

import asyncio
import msgspec
import redis.asyncio as redis_async
from loguru import logger


_PANIC_KEY_ENV = "POLARIS_PANIC_KEY"
_HALT_REDIS_KEY = "prometheus:trading_halted"
_RESUME_REDIS_KEY = "prometheus:resume_trigger"
_HALT_CHANNEL = "prometheus:system_halt"


class KillSwitch:
    def __init__(self, redis_client: redis_async.Redis, audit_log_path: Path) -> None:
        self._redis = redis_client
        self._audit_path = audit_log_path

    def _expected_key(self) -> str | None:
        """Scoped os.environ read — documented override per Invariant 3."""
        return os.environ.get(_PANIC_KEY_ENV)

    def verify_panic_key(self, provided: str) -> bool:
        """Constant-time HMAC comparison — timing-attack safe."""
        expected = self._expected_key()
        if expected is None:
            logger.error("panic key not configured — halt BLOCKED")
            return False
        return hmac.compare_digest(provided.encode(), expected.encode())

    async def halt(self, reason: str, triggered_by: str, details: dict) -> None:
        """Canonical halt — sets Redis key, publishes channel event, writes audit."""
        event = SystemHaltEvent(
            event_type="HALT",
            reason=reason,  # type: ignore[arg-type]
            triggered_by=triggered_by,
            timestamp_iso=datetime.utcnow().isoformat(),
            details={k: str(v) for k, v in details.items()},
        )
        encoded = msgspec.json.encode(event)

        # 1. Persistent halt flag
        await self._redis.set(_HALT_REDIS_KEY, encoded)
        # 2. Broadcast to all subscribers (reconciler, dashboard, etc.)
        await self._redis.publish(_HALT_CHANNEL, encoded)
        # 3. Audit trail to disk (no aiofiles)
        await self._append_audit(encoded + b"\n")

        logger.critical("SYSTEM HALTED", reason=reason, triggered_by=triggered_by)

    async def resume(self, authenticated_key: str) -> bool:
        """One-shot resume — requires fresh panic key each time."""
        if not self.verify_panic_key(authenticated_key):
            logger.error("resume denied — invalid panic key")
            return False
        event = SystemHaltEvent(
            event_type="RESUME",
            reason="MANUAL_RESUME",
            triggered_by="human_operator",
            timestamp_iso=datetime.utcnow().isoformat(),
            details={},
        )
        encoded = msgspec.json.encode(event)
        await self._redis.delete(_HALT_REDIS_KEY)
        await self._redis.set(_RESUME_REDIS_KEY, encoded, ex=60)
        await self._redis.publish(_HALT_CHANNEL, encoded)
        await self._append_audit(encoded + b"\n")
        logger.warning("SYSTEM RESUMED")
        return True

    async def is_halted(self) -> bool:
        return await self._redis.exists(_HALT_REDIS_KEY) > 0

    async def _append_audit(self, payload: bytes) -> None:
        """Disk write via asyncio.to_thread — no aiofiles."""
        def _append() -> None:
            with self._audit_path.open("ab") as f:
                f.write(payload)
        await asyncio.to_thread(_append)
```

## Task 3 — Retry Budget

Create `prometheus/kill_switch/retry_budget.py`:

```python
from dataclasses import dataclass, field
import time


@dataclass
class RetryBudget:
    total_budget_seconds: float = 120.0   # 2 minutes, NOT 10 minutes
    escalation_threshold_seconds: float = 60.0   # halt + page at 1 min
    _elapsed: float = 0.0
    _last_check: float = field(default_factory=time.monotonic)

    def tick(self) -> float:
        now = time.monotonic()
        self._elapsed += now - self._last_check
        self._last_check = now
        return self._elapsed

    def should_escalate(self) -> bool:
        return self._elapsed >= self.escalation_threshold_seconds

    def budget_exhausted(self) -> bool:
        return self._elapsed >= self.total_budget_seconds
```

**Budget semantics:**
- `escalation_threshold` (60s): log critical + notify operator.
- `total_budget` (120s): trigger kill switch HALT automatically.

The old 600-second budget was too permissive — 10 minutes of retry storms can
accumulate severe losses. 2 minutes is the ceiling.

## Task 4 — Triggers

Create `prometheus/kill_switch/triggers.py`:

Each trigger is a pure function that returns `(should_halt: bool, reason: str, details: dict)`:

-spike_detector_trigger(pnl_history) — if rolling 1-minute P&L < Decimal("-0.03")
- `position_limit_trigger(positions, max_positions)` — if count > max
- `api_failure_storm_trigger(retry_budget)` — if budget exhausted
- liquidation_proximity_trigger(positions, threshold_pct=Decimal("0.85")) — if any position is within Decimal("0.15") of liquidation price

Integration with the main event loop: a background task evaluates all triggers
every 500ms and calls `kill_switch.halt(...)` if any returns `should_halt=True`.

## Task 5 — HTTP Panic Endpoint

Create `prometheus/kill_switch/http_endpoint.py`:

FastAPI (not Flask) route `POST /kill-switch/halt`:

- Requires `X-Panic-Key` header.
- Verifies via `kill_switch.verify_panic_key()` — `hmac.compare_digest`.
- On success: calls `halt(reason="MANUAL_PANIC_KEY", triggered_by=request.client.host, ...)`.
- Returns HTTP 200 with halt confirmation.
- On failure: HTTP 403, no details leaked.

Rate limit: 3 attempts per minute per IP (prevents brute-force timing).

## Task 6 — Tests

Create `prometheus/kill_switch/test_kill_switch.py`:

- `test_halt_sets_redis_key_and_publishes_channel`
- `test_resume_requires_fresh_panic_key`
- `test_panic_key_verification_uses_constant_time` — patches `hmac.compare_digest`
  and asserts it was called, then verifies `==` is NOT used anywhere (grep test).
- `test_audit_log_written_on_halt` — writes to tmp path, verifies bytes.
- `test_retry_budget_escalates_at_60s`
- `test_retry_budget_exhausted_at_120s`
- `test_is_halted_reads_redis_key`
- `test_spike_detector_trigger` — known P&L series → expected halt decision.
- `test_http_panic_endpoint_rate_limited` — 4th attempt in 60s returns 403.
- `test_http_panic_endpoint_rejects_bad_key` — returns 403.
- `test_audit_write_uses_asyncio_to_thread` — mock `asyncio.to_thread`, verify called.
- `test_no_aiofiles_import` — grep-based.

## Quality Gates
1. `pytest prometheus/kill_switch/ -v` — all pass.
2. `pyright --pythonversion 3.12 prometheus/kill_switch/` — zero errors.
3. `grep -rn "aiofiles" prometheus/kill_switch/` — zero results.
4. `grep -rn "expected.*==.*provided\|provided.*==.*expected" prometheus/kill_switch/core.py` — zero
   (no string-equality key comparison).
5. `grep -rn "hmac.compare_digest" prometheus/kill_switch/core.py` — at least one result.
6. `grep -rn "os.environ\|os.getenv" prometheus/kill_switch/core.py` — limited to the single
   scoped access in `_expected_key()`.

## Anti-Pattern Checklist
- [ ] HMAC uses `hmac.compare_digest()` — NEVER `==`
- [ ] `os.environ` access limited to panic-key read in `_expected_key()`
- [ ] All other settings via `PolarisSettings`
- [ ] No `aiofiles` — `asyncio.to_thread(path_write...)` only
- [ ] Separate halt and resume keys (halt is persistent, resume is one-shot)
- [ ] `SystemHaltEvent` is `msgspec.Struct`, not a bare dict
- [ ] Published to canonical `prometheus:system_halt` channel
- [ ] Retry budget capped at 120s total, escalation at 60s (not 600/10-min)
- [ ] HTTP endpoint rate-limited (3/min/IP)
- [ ] No ATLAS imports — standalone module
- [ ] All functions ≤ 40 lines
