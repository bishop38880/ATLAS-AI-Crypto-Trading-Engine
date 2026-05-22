# SESSION: Query Classifier (FINCON & Aggregator Aligned)

## Context Files
@atlas/shared/config.py @pipeline/data_aggregator.py

## Goal
Classify every incoming reasoning request to determine the correct retrieval strategy (`RAG_ONLY`, `MCP_ONLY`, `HYBRID`, `DIRECT`) and retrieval depth (`SHALLOW`, `DEEP`, `DEFAULT`). This prevents wasted database queries and stops live APIs from being hammered for historical questions.

**ARCHITECTURAL ALIGNMENT:** 1. **Dynamic Assets:** Do NOT hardcode the 33 assets. The orchestrator passes the `asset` parameter dynamically.
2. **Context Assembler is DEAD:** This classifier feeds directly into the `DataAggregator` (Session 20), which feeds the `SignalSynthesiserAgent` (Session 13). Do not build a standalone assembler.

---

## Task 1 — Data Models
Create `pipeline/query_classifier.py`:
- Import `RetrievalDepth` enum (from Session 21 setup).
- Implement `QueryClassification` (frozen Pydantic model):
  - `route: Literal["RAG_ONLY", "MCP_ONLY", "HYBRID", "DIRECT"]`
  - `confidence: float`
  - `detected_signals: list[str]`
  - `retrieval_depth: RetrievalDepth`
  - `query_hash: str`

## Task 2 — Classification Constants
Define these at the module level for testing:
- `DIRECT_KEYWORDS = ("explain", "what is", "define", "how does")`
- `REALTIME_KEYWORDS = ("current", "now", "latest", "live", "today")`
- `HISTORICAL_KEYWORDS = ("last time", "historically", "past", "similar setup", "pattern")`
- `DEEP_ESCALATION_PHRASES = ("multi-factor", "cross-asset", "conflicting signal", "rotation pool")`

## Task 3 — Classification Logic
Implement `async def classify(self, query_text: str, asset: str) -> QueryClassification`:
1. **Step A (DIRECT):** If `query` contains DIRECT_KEYWORDS and does *not* contain the `asset` string -> `route=DIRECT`, `depth=SHALLOW`.
2. **Step B (HYBRID):** If `query` contains both REALTIME and HISTORICAL keywords -> `route=HYBRID`.
3. **Step C (REAL-TIME):** If `query` contains REALTIME_KEYWORDS -> `route=MCP_ONLY`.
4. **Step D (HISTORICAL):** If `query` contains HISTORICAL_KEYWORDS -> `route=RAG_ONLY`.
5. **Depth Escalation:** If `query` contains `DEEP_ESCALATION_PHRASES`, force `retrieval_depth=RetrievalDepth.DEEP`. Otherwise, map `DIRECT`/`MCP_ONLY` to `SHALLOW` and `RAG_ONLY`/`HYBRID` to `DEFAULT`.
6. **Fallback:** If nothing matches, default to `HYBRID` / `DEFAULT` with low confidence.

## Task 4 — Redis Caching
Implement `_get_cached_classification` and `_cache_classification`:
- Use `hashlib.sha256` to create a 16-char hash of the query text.
- Read/write to Redis key `qclassify:{query_hash}` with a 300s TTL.
- Fail silently (best-effort caching) if Redis throws a ConnectionError.

## Quality Gates
1. `pytest pipeline/test_query_classifier.py -v --tb=short`
   - Test: "explain funding rates" -> `DIRECT`, `SHALLOW`
   - Test: "current funding rate for BTC" -> `MCP_ONLY`, `SHALLOW`
   - Test: "compare current setup to historical" -> `HYBRID`, `DEFAULT`
   - Test: "cross-asset analysis of past setups" -> `RAG_ONLY`, `DEEP`
2. `pyright pipeline/query_classifier.py --pythonversion 3.12` — zero errors.
3. `grep -i "contextassembler" pipeline/query_classifier.py` MUST return zero results.