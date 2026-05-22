# SESSION 24 — Agent Zero: Memory Lifecycle & Garbage Collection (ATLAS)

## Context Files
@atlas/rag/writer.py @atlas/rag/query.py @atlas/shared/config.py @schema.sql @atlas/core/cycle_scheduler.py

## Prerequisites
Sessions 11 (RAG memory) and 11A (Post-Trade Learning) must be complete.

## Goal
Build "Agent Zero" — a nightly background task that evaluates every document in RAG
memory. It computes an `Escore` (Memory Usefulness Score) based on recency, retrieval
frequency, trade outcome, and verification flags. Documents scoring below 0.75 are
soft-deleted to keep the context window pure and database latency low.

**ARCHITECTURAL ALIGNMENT:**
1. Agent Zero executes against PostgreSQL `signal_history` via `asyncpg` AND marks archived vectors in Qdrant with metadata flag `archived=true`.
2. Runs at 02:00 UTC, cleaning the database BEFORE the Session 11B Pattern Extractor runs at 00:00 UTC (note: Pattern Extractor should be moved to 03:00 UTC to follow Agent Zero).
3. RAG queries must filter `archived=false` by default.

---

## NON-NEGOTIABLE INVARIANTS
1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` → `msgspec`. `pandas` → banned. `FAISS`/`BM25`/`pgvector`/`SQLAlchemy` → banned.
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru only.**
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **Non-blocking:** Agent Zero runs as an `asyncio.Task`. If DB locks or Redis times out, it silently fails and retries tomorrow. NEVER crash the Multi-Asset Runner.

---

## Task 1 — Config & Data Models

Create `atlas/rag/agent_zero/models.py`:
- `EscoreWeights` (frozen Pydantic): `recency: float = 0.25`, `retrieval_frequency: float = 0.30`, `outcome_quality: float = 0.35`, `contradiction_free: float = 0.10`
- `@model_validator` ensuring weights sum to exactly 1.0
- `EscoreRecord` for audit logging
- Add `agent_zero_threshold: float = 0.75` to `PolarisSettings`

## Task 2 — The Escore Calculator

Create `atlas/rag/agent_zero/scorer.py`:
- `EscoreCalculator.calculate(doc_metadata: dict) -> EscoreRecord`
- **Recency (w1):** <24h=1.0 | 1-7d=0.8 | 7-30d=0.5 | 30-90d=0.3 | >90d=0.1
- **Frequency (w2):** Read `rag:retrieval_count:{doc_id}` from Redis. ≥10=1.0 | 5-9=0.7 | 2-4=0.4 | 1=0.2 | 0=0.0
- **Outcome (w3):** From PostgreSQL `outcome_classification`. GOOD_WIN=1.0 | GOOD_LOSS=0.7 | LUCKY_WIN=0.5 | PENDING=0.5 | BAD_LOSS=0.0
- **Contradiction (w4):** Read `rag:contradiction_count:{doc_id}` from Redis. 0=1.0 | 1=0.5 | >1=0.0
- If Escore < threshold → `should_archive = True`

## Task 3 — The Memory Archiver

Create `atlas/rag/agent_zero/archiver.py`:
- `MemoryArchiver.run_nightly_cycle() -> ArchiveSummary`
- Fetch all active document metadata from PostgreSQL `signal_history` via `asyncpg`
- Batch-fetch Redis retrieval/contradiction counters (via `redis.asyncio`)
- Compute Escores
- **PostgreSQL:** Bulk `UPDATE` to set `archived=TRUE`, `archived_at=NOW()`, `escore=value`
- **Qdrant:** Update metadata on archived vectors to set `archived=true` (so RAG queries can filter them out)
- Log aggregate stats to `ArchiveSummary`

## Task 4 — The Scheduler

Create `atlas/rag/agent_zero/scheduler.py`:
- Background `asyncio.Task` sleeping until 02:00 UTC daily
- Broad `try/except` — DB lock or Redis timeout logs ERROR and retries next night
- Wire into `atlas/core/cycle_scheduler.py` alongside Pattern Extractor
- Pattern Extractor should be rescheduled to 03:00 UTC (after Agent Zero cleans)

## Task 5 — PostgreSQL Schema Additions

Append to `schema.sql`:
```sql
ALTER TABLE signal_history
    ADD COLUMN IF NOT EXISTS archived BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS escore NUMERIC(6,4) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_active_signal_history
    ON signal_history (asset, created_at DESC)
    WHERE archived = FALSE;

CREATE TABLE IF NOT EXISTS rag_archive_log (
    id BIGSERIAL PRIMARY KEY,
    run_at TIMESTAMPTZ NOT NULL,
    total_scored INTEGER NOT NULL,
    archived_count INTEGER NOT NULL,
    retained_count INTEGER NOT NULL,
    avg_escore NUMERIC(6,4) NOT NULL
);
```

## Task 6 — Update RAG Queries

Update `atlas/rag/query.py`:
- All default queries must filter `WHERE archived = FALSE` in PostgreSQL
- Qdrant searches must include metadata filter `archived != true`

## Quality Gates
1. `pytest atlas/rag/agent_zero/test_agent_zero.py -v` — all pass
   - Test: document >90d old with 0 retrievals → archived
   - Test: recent document with high retrieval count → retained
   - Test: BAD_LOSS outcome drags Escore below threshold
   - Test: scheduler failure doesn't crash main loop
2. `pyright --pythonversion 3.12 atlas/rag/agent_zero/` — zero errors

## Anti-Pattern Checklist
- [ ] No `import aioredis` — use `redis.asyncio`
- [ ] No `import json` — use `msgspec`
- [ ] No `SQLAlchemy` — use `asyncpg`
- [ ] No `pgvector` direct — archive flag on Qdrant metadata
- [ ] No `os.getenv()` — use `PolarisSettings`
- [ ] Scheduler failure never crashes the runner
- [ ] All functions ≤ 40 lines
