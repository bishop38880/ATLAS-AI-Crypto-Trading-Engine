# SESSION 05 — Two-Tier Semantic Cache: Exact Match + Qdrant Similarity

## Context Files
@atlas/rag/ @atlas/shared/config.py @atlas/core/registry.py @atlas/core/embedding_client.py

## Prerequisites
Sessions 01–04A complete. The `MistralEmbeddingClient` must be available (built in Session 06, but the interface is defined here for cache integration).

**DEPENDENCY NOTE:** This session defines the cache structure. Session 06 builds the `MistralEmbeddingClient`. If running in strict order, stub the embedding client interface here and implement it fully in Session 06. The cache must be designed for 1024-dim vectors from day one.

## Goal
Build a two-tier cache that eliminates redundant LLM calls. Tier 1 is
exact-match Redis (sub-millisecond). Tier 2 is Qdrant cosine similarity
for semantically equivalent queries. Target: 30-50% cache hit rate.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `RRF`, `pgvector`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers`, `LlamaIndex` are all **BANNED**.
   - Redis: `redis.asyncio`
   - JSON: `msgspec`
   - PostgreSQL: `asyncpg`
   - Vector search: **Qdrant** (primary) + **LanceDB** (hot standby via `asyncio.to_thread()`)
   - Embeddings: **`MistralEmbeddingClient`** (1024-dim cloud API, zero VRAM)
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru only.**
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **Mathematical Precision Boundary:** Embedding vectors use `float` (never `Decimal`). Financial calculations use `Decimal`. These are separate domains — do not mix them.
9. **Embedding dimension: 1024.** All vectors are 1024-dim from `MistralEmbeddingClient`. If you see any reference to 384-dim, `all-MiniLM-L6-v2`, or `sentence-transformers`, that is **STALE** — ignore it completely.

---

## Task 1 — Embedding Service Interface

Create `atlas/rag/embedding_service.py`:

- Class `EmbeddingService` that wraps `MistralEmbeddingClient` (from Session 06)
- Method: `async def embed(text: str) -> list[float]` — returns 1024-dim vector
- Method: `async def embed_batch(texts: list[str]) -> list[list[float]]`
- If `MistralEmbeddingClient` is not yet built (Session 06 not complete), create a protocol/interface stub that returns 1024-dim zero vectors and logs a warning
- **Do NOT import `sentence-transformers`.** Do NOT use `all-MiniLM-L6-v2`. These are permanently removed from the stack.

## Task 2 — Exact Match Cache (Tier 1)

Create `atlas/rag/cache_exact.py`:

- Class `ExactMatchCache`
- Uses `redis.asyncio` (never `aioredis`)
- Key pattern: `cache:exact:{sha256_of_prompt}`
- Value: `msgspec`-encoded `CacheEntry(response: str, created_at: float, provider: str)`
- TTL inherited from the underlying provider's TTL
- Methods:
  - `async def get(prompt: str) -> CacheEntry | None`
  - `async def set(prompt: str, response: str, provider: str, ttl_seconds: int) -> None`
  - `async def invalidate_provider(provider: str) -> int` — deletes all entries tagged with that provider

## Task 3 — Semantic Cache (Tier 2)

Create `atlas/rag/cache_semantic.py`:

- Class `SemanticCache`
- **Storage: Qdrant collection** `semantic_cache` with HNSW index (1024-dim vectors)
- Qdrant collection config:
  ```python
  # Collection creation (run once at startup)
  await qdrant_client.create_collection(
      collection_name="semantic_cache",
      vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
  )
  ```
- Similarity threshold: 0.93 default, configurable per category:
  - `sentiment`: 0.90 (more permissive)
  - `market_analysis`: 0.95 (stricter)
- **LanceDB hot standby:** If Qdrant is unavailable, fall back to LanceDB for semantic search. LanceDB operations are synchronous — wrap in `asyncio.to_thread()`.
- Methods:
  - `async def search(prompt: str, category: str) -> CacheEntry | None`
  - `async def store(prompt: str, response: str, provider: str, category: str, ttl: int) -> None`
  - `async def cleanup_expired() -> int` — delete entries past `expires_at`
- Metadata stored with each vector: `prompt_text`, `response_text`, `provider`, `category`, `created_at`, `expires_at`

## Task 4 — Unified Cache Lookup

Create `atlas/rag/cache_manager.py`:

- Class `CacheManager` combining both tiers
- Lookup order: Tier 1 exact → Tier 2 semantic → cache miss
- Method: `async def lookup(prompt: str, category: str) -> CachedResponse | None`
- Method: `async def store(prompt: str, response: str, provider: str, category: str, ttl: int) -> None`
  - Writes to BOTH tiers simultaneously
- Method: `async def invalidate_provider(provider: str) -> None`
  - Called by Validation Gate when a provider goes DEGRADED
  - Clears exact cache entries AND marks semantic entries expired
- Log cache hit/miss with tier info for monitoring

## Quality Gates
1. `pytest atlas/rag/test_cache_exact.py -v` — all pass
   - Test: store and retrieve works
   - Test: TTL expiration works
   - Test: provider invalidation clears entries
2. `pytest atlas/rag/test_cache_semantic.py -v` — all pass
   - Test: identical prompt → cache hit (similarity = 1.0)
   - Test: paraphrased prompt → cache hit (similarity > 0.93)
   - Test: different topic prompt → cache miss (similarity < 0.93)
   - Test: expired entry → cache miss
   - Test: Qdrant unavailable → LanceDB fallback works
3. `pytest atlas/rag/test_cache_manager.py -v` — all pass
   - Test: Tier 1 hit returns without querying Tier 2
   - Test: provider invalidation clears both tiers
4. `pyright --pythonversion 3.12 atlas/rag/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `sentence-transformers` or `all-MiniLM-L6-v2` — use `MistralEmbeddingClient`
- [ ] No `vector(384)` — all vectors are 1024-dim
- [ ] No `pgvector` — use Qdrant + LanceDB
- [ ] No `FAISS` or `BM25` or `RRF`
- [ ] No `SQLAlchemy` — use `asyncpg` for PostgreSQL
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] LanceDB calls wrapped in `asyncio.to_thread()`
- [ ] Embedding vectors use `float`, not `Decimal`
- [ ] All functions ≤ 40 lines
