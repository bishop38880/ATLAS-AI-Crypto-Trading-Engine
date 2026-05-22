# SESSION 21 — RAG Upgrades: Freshness Decay + Adaptive Retrieval Depth

## Context Files
`@atlas/rag/query.py  @atlas/shared/config.py  @pipeline/query_classifier.py`
`@POLARIS_Context_Document_v2.1.md` — read fully before any code.

---

## Hard-Wall Invariants (restated — verify before writing code)

1. POLARIS has ZERO exchange awareness. No CCXT, no order logic.
2. Vector stack: **Qdrant (primary) + LanceDB (hot standby)**. pgvector,
   FAISS, BM25, RRF, LlamaIndex are **banned**.
3. `redis.asyncio` (not `aioredis`). `msgspec` (not stdlib `json`).
   `asyncpg` (not SQLAlchemy). `pyright` (not `mypy`). Loguru with
   positional format (not keyword args).
4. `Decimal` for financial fields. `float` acceptable for similarity
   scores, decay weights, confidence.
5. `PolarisSettings` only — no `os.getenv()`.
6. Max 40 lines per function. Pydantic v2 frozen models.
7. All LanceDB calls wrapped in `asyncio.to_thread()`.
8. pytest floor — read current count at session start; it must not
   decrease.

Confirm all 8 invariants before I give the task.

---

## Goal

Two targeted upgrades to the existing Qdrant-based RAG pipeline to
fix retrieval quality gaps:

1. **Freshness Decay** — Older documents lose relevance exponentially.
   A 48h-old signal contributes far less context than a 2h-old one.
2. **Adaptive Retrieval Depth** — Simple queries get fast, shallow
   searches. Complex queries get deep, exhaustive searches with higher
   HNSW `ef` values for better recall.

**Architectural alignment:** Target the Qdrant `RAGQueryEngine`
(with LanceDB fallback path). Do NOT reintroduce pgvector, FAISS,
BM25, or RRF under any framing.

---

## Task 1 — Config Additions

Update `atlas/shared/config.py` (`PolarisSettings` / `ModelStackConfig`):

```python
# Freshness decay
rag_freshness_enabled: bool = True
rag_freshness_lambda_per_hour: float = 0.05
    # Per-hour exponential decay constant.
    # 0.05/hr → 2h weight 0.90, 12h weight 0.55, 24h weight 0.30, 48h weight 0.09.
    # Tune after backtesting; too aggressive (> 0.10) zeroes day-old regime context.

# Adaptive retrieval depth
rag_shallow_top_k: int = 3
rag_default_top_k: int = 10
rag_deep_top_k: int = 15

# Qdrant hnsw_ef tuning per depth — higher ef = better recall, slower
rag_hnsw_ef_shallow: int = 50
rag_hnsw_ef_default: int = 100
rag_hnsw_ef_deep: int = 150
```

Explicit naming (`_per_hour`) prevents unit ambiguity. The previous
unnamed `lambda` meant no one could tell whether decay was per-minute,
per-hour, or per-day without reading the formula.

---

## Task 2 — Freshness Decay in RAG Query Engine

Update `atlas/rag/query.py` (`RAGQueryEngine`).

**Critical math note:** Qdrant returns **similarity** (higher is
better — cosine similarity ∈ [-1, 1], or dot product unbounded).
LanceDB fallback returns **distance** (lower is better). The decay
must be applied to similarity scores, so the LanceDB path must first
convert distance → similarity.

```python
def _apply_freshness_decay(
    self,
    documents: list[ScoredDocument],
    now: datetime,
) -> list[ScoredDocument]:
    """Re-rank documents by time-decayed similarity.

    final_score = similarity_score * exp(-lambda_per_hour * age_hours)

    Guards:
      - If freshness disabled, returns input unchanged.
      - Clock skew (age < 0) is clamped to 0 — future documents get weight 1.0.
      - Very old documents (age > 1 week) are clamped at the 1-week weight
        to avoid underflow.

    Args:
        documents: Scored documents where `similarity_score` is
                   cosine similarity in [0, 1] (NOT distance).
        now: Current UTC datetime for age calculation.

    Returns:
        Documents with `final_score` populated, sorted descending.
    """
    if not self._settings.rag_freshness_enabled:
        for d in documents:
            d.final_score = d.similarity_score
        return documents

    lam = self._settings.rag_freshness_lambda_per_hour
    one_week_hours = 168.0

    for doc in documents:
        age = (now - doc.timestamp).total_seconds() / 3600.0
        age_clamped = max(0.0, min(age, one_week_hours))
        weight = math.exp(-lam * age_clamped)
        doc.final_score = doc.similarity_score * weight

    return sorted(documents, key=lambda d: d.final_score, reverse=True)
```

**LanceDB fallback adapter (separate helper):**

```python
def _lancedb_distance_to_similarity(self, distance: float) -> float:
    """LanceDB cosine distance → similarity.

    For unit-normalised vectors, cosine_distance ∈ [0, 2] where
    0 = identical, 2 = opposite. similarity = 1 - (distance / 2).
    """
    return max(0.0, 1.0 - (distance / 2.0))
```

Apply this conversion before `_apply_freshness_decay` on the LanceDB
path so both backends feed the decay function in the same score space.

---

## Task 3 — Adaptive Retrieval Depth

Update `atlas/rag/query.py`:

```python
from enum import Enum

class RetrievalDepth(str, Enum):
    SHALLOW = "shallow"
    DEFAULT = "default"
    DEEP    = "deep"
```

Update `find_similar_contexts` to accept `retrieval_depth: RetrievalDepth`:

**Qdrant path** — pass `limit` and `search_params` with `hnsw_ef`:

```python
from qdrant_client.models import SearchParams

limit, ef = self._depth_to_params(retrieval_depth)

qdrant_results = await self._qdrant.search(
    collection_name=self._collection,
    query_vector=embedding,
    limit=limit,
    search_params=SearchParams(hnsw_ef=ef),
    with_payload=True,
    query_filter=qdrant_filter,
)
```

**LanceDB fallback path** — LanceDB has no direct `ef` control; use
`nprobes` for IVF_PQ indexes, approximated from depth:

```python
# asyncio.to_thread wrap — LanceDB client is sync
def _sync_lance_search() -> list[dict]:
    return (
        self._lance_table
        .search(embedding)
        .limit(limit)
        .nprobes(20 if retrieval_depth == RetrievalDepth.DEEP else 10)
        .to_list()
    )

lance_results = await asyncio.to_thread(_sync_lance_search)
```

**Depth-to-params helper (< 15 lines):**

```python
def _depth_to_params(self, depth: RetrievalDepth) -> tuple[int, int]:
    """Return (top_k, hnsw_ef) for a given retrieval depth."""
    s = self._settings
    if depth == RetrievalDepth.SHALLOW:
        return s.rag_shallow_top_k, s.rag_hnsw_ef_shallow
    if depth == RetrievalDepth.DEEP:
        return s.rag_deep_top_k, s.rag_hnsw_ef_deep
    return s.rag_default_top_k, s.rag_hnsw_ef_default
```

---

## Task 4 — Wire Depth into Query Classifier

Update `pipeline/query_classifier.py`:

- Add `retrieval_depth: RetrievalDepth = RetrievalDepth.DEFAULT` to
  `QueryClassification` (frozen Pydantic model).
- Map route types to depth:
  - `DIRECT` / `MCP_ONLY` → `SHALLOW` (cheap, well-scoped queries)
  - `MULTI_AGENT` → `DEFAULT`
  - Multi-factor phrases detected → `DEEP`
- Multi-factor phrases (case-insensitive): `"cross-asset"`,
  `"conflicting signal"`, `"multi-factor"`, `"regime shift"`,
  `"correlation breakdown"`, `"compare with history"`.

Add tests for each phrase escalating to DEEP.

---

## Task 5 — Orchestrator Integration

Update `atlas/orchestrator/` (path per `010-atlas-identity.mdc`; verify
actual orchestrator module path in the repo before editing):

- Pass `retrieval_depth` from `QueryClassification` directly into the
  `RAGQueryEngine.find_similar_contexts()` call.
- Log depth and freshness params at each retrieval:

```python
logger.info(
    "rag_query | depth={} | top_k={} | ef={} | freshness_lambda={} | found={}",
    retrieval_depth.value, top_k, ef,
    settings.rag_freshness_lambda_per_hour, len(results),
)
```

Langfuse trace metadata should include the same fields so retrieval
behaviour is visible in the LLM observability dashboard.

---

## Quality Gates

1. `pytest atlas/rag/test_query.py -v --tb=short` — new tests:
   - `test_freshness_decay_reranks_old_docs_below_new` — two docs with
     identical similarity, one 2h old, one 48h old → 2h wins.
   - `test_freshness_disabled_returns_unchanged_order` — flag off, order preserved.
   - `test_clock_skew_future_doc_weight_is_1_0`.
   - `test_lance_distance_converted_to_similarity_before_decay`.
   - `test_depth_shallow_uses_top_k_3_ef_50`.
   - `test_depth_deep_uses_top_k_15_ef_150`.
2. `pytest pipeline/test_query_classifier.py -v --tb=short` —
   verify every multi-factor phrase escalates to DEEP.
3. `grep -rn "pgvector\|FAISS\|BM25\|RRF\|LlamaIndex" atlas/rag/ pipeline/` —
   MUST return zero results.
4. `grep -rn "aioredis\|orjson\|import json\|os.getenv" atlas/rag/ pipeline/` —
   MUST return zero results.
5. `pyright atlas/rag/ pipeline/ --pythonversion 3.12` — zero errors.
6. pytest floor must not decrease from session-start count.

---

## Notes for the Agent

- The previous version of this session targeted pgvector. That was
  correct at Session 11 but the architecture has since moved to Qdrant.
  Do NOT be misled by older session files that still reference pgvector.
- `rag_freshness_lambda_per_hour = 0.05` is a starting point. Treat it
  as a hyperparameter — the correct value depends on signal half-life
  and should be swept against backtested retrieval quality once paper
  trading outcome data is available.
- The distance-to-similarity conversion on the LanceDB path is the
  single most error-prone part of this session. Write the test for
  that conversion first, before touching the decay math.
