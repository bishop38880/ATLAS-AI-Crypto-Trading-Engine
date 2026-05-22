# SESSION 26 — Position Reconciliation Loop

## Context Files
@prometheus/oms/ @prometheus/execution/bitget_execution_client.py @prometheus/kill_switch/ @atlas/shared/config.py @schema.sql

## Prerequisites
- Session 22 (Kill Switch) operational — canonical `prometheus:system_halt`
  channel and `SystemHaltEvent` schema defined.
- **Session 22B (BitgetExecutionClient) — hard prerequisite.** The reconciler
  reads live state via this client's `list_open_positions()` and
  `list_open_plan_orders()` methods.
- Session 23 — paper-trading guard integration.
- Session 25 — stop ladder tables exist (`stop_ladder_tiers`).

## Goal
Run a 60-second loop comparing PROMETHEUS OMS state against the live Bitget
account state. Silent divergence from partial fills, rejected orders, or
network blips is a real account-wipeout risk. The reconciler detects, alerts,
and in critical cases triggers the canonical system halt — it never closes or
opens positions itself.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.**
2. **No banned libraries.** `aioredis` → `redis.asyncio`; stdlib `json` →
   `msgspec`; all the usual bans.
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru POSITIONAL format only.**
6. **Decimal everywhere.** `asyncpg` takes `Decimal` directly.
7. **`BitgetExecutionClient` only.** No `BitgetDirectClient` here —
   `BitgetDirectClient` is the kill-switch's reserved client.
8. **Reconciler does not close or open positions.** It publishes discrepancies
   and, in critical cases, emits a `SystemHaltEvent` to the canonical
   `prometheus:system_halt` Pub/Sub channel. The kill switch (Session 22) acts
   on the halt event. This invariant is absolute.
9. **CRITICAL — COMPARE ENTRY NOTIONALS, NOT MARK-PRICE NOTIONALS.** Bitget's
   `size` field is in **base coin**. Naive conversion via `size × markPrice`
   flags every valid position as divergent the moment price moves. Compare
   entry notionals:
   ```
   bitget_notional_at_entry = bitget_pos.total × bitget_pos.average_open_price
   ```
   OMS recorded USD-at-entry. This is an apples-to-apples comparison.
10. **Phantom persistence.** A `PHANTOM_POSITION` (Bitget has it, OMS doesn't)
    is a critical discrepancy but CAN be a transient race — e.g. the trade
    just opened and the OMS row hasn't committed yet. Do not halt on the first
    detection. Halt only if the phantom persists across **two consecutive
    reconciliation runs**.
11. **SIDE_MISMATCH and MARGIN_MODE_WRONG halt immediately.** These cannot be
    transient — the ledger has a bug or the exchange is in an unexpected state.
12. **60-second interval.** The loop uses `asyncio.sleep` with jitter ± 5s to
    avoid thundering-herd on the Bitget API if multiple services restart
    simultaneously.

---

## Task 1 — Models

Create `prometheus/reconciliation/models.py`:

```python
from decimal import Decimal
from datetime import datetime
from enum import Enum
from typing import Literal
from pydantic import BaseModel


class DiscrepancyType(str, Enum):
    POSITION_MISMATCH   = "POSITION_MISMATCH"      # OMS has position, Bitget doesn't (or wrong side)
    PHANTOM_POSITION    = "PHANTOM_POSITION"       # Bitget has position, OMS doesn't know
    SIZE_DIVERGENCE     = "SIZE_DIVERGENCE"        # Entry-notional sizes differ > tolerance
    SIDE_MISMATCH       = "SIDE_MISMATCH"          # OMS says long, Bitget says short
    STOP_MISSING        = "STOP_MISSING"           # Stop ladder tier missing on Bitget
    MARGIN_MODE_WRONG   = "MARGIN_MODE_WRONG"      # Cross margin detected (prohibited)


class ReconciliationDiscrepancy(BaseModel, frozen=True):
    asset: str
    discrepancy_type: DiscrepancyType
    oms_value: str                       # human-readable OMS state
    bitget_value: str                    # human-readable Bitget state
    severity: Literal["low", "medium", "high", "critical"]
    auto_action_taken: str | None
    requires_human: bool
    detected_at: datetime


class ReconciliationReport(BaseModel, frozen=True):
    run_at: datetime
    duration_ms: int
    oms_positions_checked: int
    bitget_positions_found: int
    discrepancies: list[ReconciliationDiscrepancy]
    all_clear: bool
    halt_triggered: bool
```

## Task 2 — Reconciler Engine

Create `prometheus/reconciliation/reconciler.py`:

```python
import asyncio
import random
import time
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import msgspec
import redis.asyncio as redis_asyncio
from loguru import logger

from prometheus.execution.bitget_execution_client import BitgetExecutionClient
from prometheus.kill_switch.schemas import SystemHaltEvent   # from Session 22
from prometheus.reconciliation.models import (
    DiscrepancyType,
    ReconciliationDiscrepancy,
    ReconciliationReport,
)


SIZE_TOLERANCE_PCT    = Decimal("0.02")        # 2% — allows minor rounding
HALT_CHANNEL          = "prometheus:system_halt"
PHANTOM_PERSISTENCE_RUNS = 2


class PositionReconciler:
    """60-second OMS ↔ Bitget reconciliation loop.

    Detects discrepancies. Publishes SystemHaltEvent for critical conditions.
    Never closes or opens positions.
    """

    RECONCILE_INTERVAL_S = 60
    JITTER_S = 5

    def __init__(
        self,
        redis_client: redis_asyncio.Redis,
        pg_pool: asyncpg.Pool,
        bitget_client: BitgetExecutionClient,       # Session 22B — NOT BitgetDirectClient
        paper_trading: bool = True,
    ) -> None:
        self._redis = redis_client
        self._pg = pg_pool
        self._bitget = bitget_client
        self._paper = paper_trading
        self._phantom_tracker: dict[str, int] = {}  # asset → consecutive phantom runs

    async def run_forever(self) -> None:
        """Main loop. Cancelled via asyncio task cancellation."""
        logger.info("reconciliation_loop_started | interval_s={}", self.RECONCILE_INTERVAL_S)
        while True:
            try:
                report = await self.reconcile_once()
                await self._persist_report(report)
                if report.halt_triggered:
                    logger.critical(
                        "RECONCILIATION_HALT_TRIGGERED | discrepancies={}",
                        len(report.discrepancies),
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("reconciliation_loop_error | exc={}", exc)
            await self._sleep_with_jitter()

    async def _sleep_with_jitter(self) -> None:
        jitter = random.uniform(-self.JITTER_S, self.JITTER_S)
        await asyncio.sleep(self.RECONCILE_INTERVAL_S + jitter)
```

### Per-Run Logic

`reconcile_once()` produces a `ReconciliationReport`:

1. Load OMS open positions from PostgreSQL.
2. Fetch live Bitget positions via `self._bitget.list_open_positions()`.
3. For each OMS position, find matching Bitget position by asset+side. Compare:
   - Side → SIDE_MISMATCH if divergent (critical, immediate halt).
   - Entry notional (see below) → SIZE_DIVERGENCE if delta > 2%.
   - Margin mode → MARGIN_MODE_WRONG if cross (critical, immediate halt).
4. For each Bitget position with no OMS row, count phantom occurrences.
   Halt only if phantom persists ≥ 2 runs.
5. For each open trade in `stop_ladder_tiers` with status='placed', verify
   the plan order still exists via `list_open_plan_orders()`.
   Missing → STOP_MISSING (medium — log, attempt re-place via Session 25).

### Entry-Notional Comparison (the critical correction)

```python
async def _check_size(
    self,
    asset: str,
    oms_pos: dict,
    bitget_pos: BitgetPosition,
) -> ReconciliationDiscrepancy | None:
    """Size divergence check using ENTRY notionals — NOT mark-price notionals."""
    oms_notional = Decimal(str(oms_pos["position_notional_usd"]))

    # Bitget `size` is base coin. Multiply by avg open price for
    # apples-to-apples USD-at-entry. Do NOT use mark_price — mark
    # moves continuously and would flag every position as divergent.
    bitget_notional_at_entry = (
        bitget_pos.total * bitget_pos.average_open_price
    )

    if oms_notional <= 0:
        return None
    divergence = abs(oms_notional - bitget_notional_at_entry) / oms_notional
    if divergence <= SIZE_TOLERANCE_PCT:
        return None

    severity = "high" if divergence > Decimal("0.10") else "medium"
    return ReconciliationDiscrepancy(
        asset=asset,
        discrepancy_type=DiscrepancyType.SIZE_DIVERGENCE,
        oms_value=f"{float(oms_notional):.2f} USD",
        bitget_value=(
            f"{float(bitget_notional_at_entry):.2f} USD "
            f"(total={float(bitget_pos.total):.6f} @ "
            f"avg_open={float(bitget_pos.average_open_price):.2f})"
        ),
        severity=severity,
        auto_action_taken=None,
        requires_human=True,
        detected_at=datetime.now(tz=timezone.utc),
    )
```

### Halt Publication

```python
async def _publish_halt(self, reason: str, details: dict) -> None:
    event = SystemHaltEvent(
        event_type="HALT",
        reason=reason,                   # Literal value from SystemHaltEvent schema
        triggered_by="reconciler",
        timestamp_iso=datetime.utcnow().isoformat(),
        details={k: str(v) for k, v in details.items()},
    )
    encoded = msgspec.json.encode(event)
    await self._redis.publish(HALT_CHANNEL, encoded)
    # Also set the persistent halt key — kill switch reads this on startup
    await self._redis.set("prometheus:trading_halted", encoded)
    logger.critical("reconciler_halt_published | reason={} | details={}", reason, details)
```

### Phantom Persistence

```python
def _track_phantom(self, asset: str, is_phantom_this_run: bool) -> bool:
    """Returns True if phantom has persisted long enough to halt."""
    if is_phantom_this_run:
        self._phantom_tracker[asset] = self._phantom_tracker.get(asset, 0) + 1
    else:
        self._phantom_tracker.pop(asset, None)
    return self._phantom_tracker.get(asset, 0) >= PHANTOM_PERSISTENCE_RUNS
```

## Task 3 — Schema

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS reconciliation_reports (
    id                     BIGSERIAL PRIMARY KEY,
    run_at                 TIMESTAMPTZ NOT NULL,
    duration_ms            INT NOT NULL,
    oms_positions_checked  INT NOT NULL,
    bitget_positions_found INT NOT NULL,
    discrepancy_count      INT NOT NULL DEFAULT 0,
    all_clear              BOOLEAN NOT NULL,
    halt_triggered         BOOLEAN NOT NULL DEFAULT FALSE,
    discrepancies          JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at             TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_recon_run_at ON reconciliation_reports(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_recon_halt ON reconciliation_reports(halt_triggered) WHERE halt_triggered;
```

## Task 4 — Tests (minimum 10)

Create `prometheus/reconciliation/test_reconciler.py`:

### Size comparison — the critical regression tests

1. **`test_size_comparison_uses_entry_notional_not_mark`** — OMS records $10k
   at entry. Mock Bitget position with `total=0.5 BTC`, `average_open_price=20_000`
   → entry notional = $10k, divergence = 0%, NO discrepancy. Then simulate
   mark price rising to 25,000 — still no discrepancy (entry notional
   unchanged). Using `size × mark_price` would flag this — test asserts we
   don't.
2. **`test_size_divergence_within_tolerance_no_flag`** — 2% or less → no flag.
3. **`test_size_divergence_10pct_high_severity`** — 12% divergence → 'high'.
4. **`test_size_divergence_5pct_medium_severity`** — 5% divergence → 'medium'.

### Critical discrepancies halt

5. **`test_side_mismatch_halts_immediately`** — OMS long, Bitget short →
   halt published on first detection.
6. **`test_margin_mode_wrong_halts_immediately`** — cross margin → halt.

### Phantom persistence

7. **`test_phantom_position_does_not_halt_first_run`** — Bitget has position,
   OMS doesn't → flagged but NO halt.
8. **`test_phantom_position_halts_after_two_consecutive_runs`** — persists
   across two runs → halt published.
9. **`test_phantom_clears_when_oms_catches_up`** — OMS commits between runs →
   counter resets; no halt.

### Wiring

10. **`test_uses_BitgetExecutionClient_not_direct`** — constructor type check;
    reconciler rejects `BitgetDirectClient`.
11. **`test_halt_published_to_canonical_channel`** — mock Redis, assert
    `publish("prometheus:system_halt", ...)` called with msgspec-encoded
    `SystemHaltEvent`.
12. **`test_persistent_halt_key_set`** — `SET prometheus:trading_halted`
    also called (kill switch reads this on startup).

### Anti-regression greps

13. `grep -rn "BitgetDirectClient" prometheus/reconciliation/` → 0.
14. `grep -rn "mark_price\|markPrice" prometheus/reconciliation/reconciler.py`
    in the size-comparison context → must not appear in any notional calc.

## Quality Gates

```bash
pytest prometheus/reconciliation/test_reconciler.py -v
pyright --pythonversion 3.12 prometheus/reconciliation/

# average_open_price is used (positive check)
grep -n "average_open_price" prometheus/reconciliation/reconciler.py
# Must return at least 1.

# No BitgetDirectClient
grep -rn "BitgetDirectClient" prometheus/reconciliation/ --include="*.py"
# Must return 0.

# Banned libraries
grep -rEn "aioredis|^import json\b|json\.loads|json\.dumps|CoinGlass" \
    prometheus/reconciliation/ --include="*.py"
# Must return 0.

# Loguru kwargs
grep -rEn 'logger\.(info|warning|error|debug|critical|exception)\([^)]*=[^)]*\)' \
    prometheus/reconciliation/ --include="*.py"
# Must return 0.

# Canonical halt channel used
grep -n "prometheus:system_halt" prometheus/reconciliation/reconciler.py
# Must return at least 1.
```

## Anti-Pattern Checklist
- [ ] `BitgetExecutionClient` — NOT `BitgetDirectClient`
- [ ] Size comparison uses `bitget_pos.total × bitget_pos.average_open_price`
- [ ] NO `mark_price` or `markPrice` in notional comparison
- [ ] Phantom position requires 2 consecutive detections before halt
- [ ] SIDE_MISMATCH and MARGIN_MODE_WRONG halt immediately
- [ ] Halt published via canonical `prometheus:system_halt` channel with
      msgspec-encoded `SystemHaltEvent`
- [ ] Persistent halt key also set (`prometheus:trading_halted`)
- [ ] Reconciler never calls `close_position` or `place_order` — only reads and publishes
- [ ] Jittered sleep prevents thundering herd on restart
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `import json` — `msgspec`
- [ ] No Loguru kwargs — positional `"{}"` format only
- [ ] `asyncio.CancelledError` re-raised in every except block
- [ ] `asyncpg` takes `Decimal` directly
- [ ] All functions ≤ 40 lines
