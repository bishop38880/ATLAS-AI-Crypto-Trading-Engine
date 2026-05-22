# SESSION 11B — Nightly Pattern Extractor (RAG Memory Consolidation / "Dream Cycle")

## Context Files
@atlas/rag/writer.py @atlas/rag/query.py @atlas/core/llm_client.py @atlas/core/cycle_scheduler.py

## Prerequisites
Session 11 (Qdrant RAG tables) and Session 11A (Post-Trade Learning) must be complete.

## Goal
Build the "Dream Cycle." ATLAS currently remembers individual trades but does not
generalize rules from them. Create a nightly background job that extracts macro-lessons
from recent outcomes and writes them to Qdrant `pattern_memory_vectors` so the Tier-3
Synthesiser can query them globally.

**ARCHITECTURAL ALIGNMENT:** Uses `DeepSeekClient` (Reasoner) for synthesis, `RAGWriter`
(Session 11) for storage. Runs completely offline — MUST NOT block the real-time scoring pipeline.

---

## NON-NEGOTIABLE INVARIANTS
1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `RRF`, `pgvector`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers`, `LlamaIndex` are all **BANNED**.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru only.**
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **Non-blocking:** The Dream Cycle runs as an `asyncio.Task` — if DeepSeek times out or the DB locks, it silently fails and retries tomorrow. It NEVER crashes the multi-asset runner.

---

## Task 1 — Trade Outcome Aggregator

Create `atlas/rag/pattern_extractor.py`:

- Class `OutcomeAggregator`
- `async def fetch_recent_outcomes(days: int = 7) -> dict[str, list[dict]]`
  - Queries PostgreSQL `signal_history` via `asyncpg` for all closed trades in last 7 days
  - Groups trades by `regime` and `outcome_classification` (e.g., all `BAD_LOSS` trades in `volatile` regime)

## Task 2 — DeepSeek Lesson Synthesis

Add to `atlas/rag/pattern_extractor.py`:

- Class `PatternSynthesiser` injected with `DeepSeekClient`
- `async def synthesize_patterns(grouped_outcomes: dict) -> list[ExtractedPattern]`
  - Prompts `deepseek-reasoner` with batches of grouped trade histories
  - System prompt: "You are a quantitative risk manager. Analyze this batch of recent trades. Identify common denominators in the losing trades and winning trades. Extract 1-3 generalized macro-lessons. Output strict JSON."
  - Output schema: `list[ExtractedPattern]` (`pattern_type`, `content_text`, `regime`, `ttl_hours`)
  - Parse response with `msgspec.json.decode()` (never stdlib `json`)

## Task 3 — RAG Memory Integration

- Method `async def store_extracted_patterns(patterns: list[ExtractedPattern]) -> None`
  - Passes patterns to `RAGWriter.write_pattern()` (Session 11)
  - This handles Mistral embedding and Qdrant HNSW insertion into `pattern_memory_vectors`

## Task 4 — The Nightly "Dream" Scheduler

Update `atlas/core/cycle_scheduler.py`:
- Add background `asyncio.Task` triggered once every 24 hours (00:00 UTC)
- Thoroughly `try/except` wrapped — if DeepSeek times out or DB locks, log ERROR and retry tomorrow
- NEVER crash the multi-asset runner
- Track the task in the scheduler's `_background_tasks: set[asyncio.Task]` with done-callbacks so exceptions don't disappear (same pattern as Session 07's SWR cache)

## Quality Gates
1. `pytest atlas/rag/test_pattern_extractor.py -v` — all pass
   - Test: aggregator correctly groups trades by regime/classification
   - Test: synthesiser handles empty data safely (no crash)
   - Test: patterns stored via RAGWriter
2. `pyright --pythonversion 3.12 atlas/rag/` — zero errors
3. No blocking I/O — must use `asyncpg` and `asyncio.Task`

## Anti-Pattern Checklist
- [ ] No `import aioredis` — use `redis.asyncio`
- [ ] No `import json` — use `msgspec`
- [ ] No `pgvector` — patterns stored in Qdrant via `RAGWriter`
- [ ] No `SQLAlchemy` — use `asyncpg`
- [ ] No `os.getenv()` — use `PolarisSettings`
- [ ] Dream cycle failure never crashes the main loop
- [ ] Background task tracked with done-callback (not fire-and-forget)
- [ ] All functions ≤ 40 lines
