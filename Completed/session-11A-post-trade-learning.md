# SESSION 11A — Post-Trade Learning MCP: PROMETHEUS → ATLAS Feedback Loop

## Context Files
@atlas/rag/pipeline.py @atlas/models/signal.py @atlas/config.py @schema.sql @backend/main.py

## Prerequisite
Session 11 (pgvector HNSW) must be complete.

## Goal
Close the intelligence loop. Build the FastAPI endpoint that allows PROMETHEUS to push trade outcomes (PnL, exit reasons) back into ATLAS's vector memory. 

**ARCHITECTURAL RULE:** This is the ONLY communication path. ATLAS never queries PROMETHEUS.

---

## Task 1 — Outcome Schema
Create `atlas/signals/outcome.py`:
- Define Enums: `ExitReason`, `MarketOutcome`, `OutcomeClassification`.
- Define `TradeOutcome` Pydantic model (`signal_id`, `pnl_pct`, `exit_reason`, etc.).

## Task 2 — Outcome Classification Logic
Create `atlas/signals/outcome_classifier.py`:
- Classify trades into 4 categories: `GOOD_WIN`, `LUCKY_WIN`, `GOOD_LOSS`, `BAD_LOSS` based on conviction vs. actual PnL outcome.

## Task 3 — FastAPI Endpoint
Create `atlas/signals/outcome_route.py` and wire to `backend/main.py`:
- Add `POST /api/outcomes/record` endpoint.
- **Write 1:** Update the original PostgreSQL signal record.
- **Write 2:** Build an enriched text document, embed via RAG pipeline, write to pgvector.
- **Write 3:** Publish learning event to Redis channel `atlas:learning_updates`.

## Task 4 — Auto-Lesson Generation
Create `atlas/signals/lesson_generator.py`:
- Deterministic template-based generator based on the `OutcomeClassification`. Creates a concise, 200-word RAG-friendly summary of why a trade won or lost.

## Task 5 — PostgreSQL Schema Update
Update `schema.sql` (or create a new migration):
- Add exit and PnL columns to `trade_signals`. Create classification index.

## Task 6 — RAG Query Enhancement
Update `RAGQueryEngine` (from Session 11):
- Add `query_similar_outcomes` to filter by asset/regime/conviction and return historical PnL win rates.

## Quality Gates
1. `pytest atlas/signals/test_outcome.py -v` — all pass.
2. `pytest atlas/signals/test_outcome_classifier.py -v` — all pass.
3. `pytest atlas/signals/test_lesson_generator.py -v` — all pass.
4. `pytest atlas/signals/test_outcome_route.py -v` — all pass.
5. `mypy --strict atlas/signals/` — zero errors.