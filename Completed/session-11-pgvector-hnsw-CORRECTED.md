# SESSION 11 — pgvector HNSW RAG Tables + Query/Write Pipeline

## Context Files
@atlas/rag/ @atlas/rag/cache_semantic.py @atlas/shared/config.py @atlas/core/embedding_client.py @atlas/core/registry.py

## Goal
Create the RAG signal memory tables with HNSW indexing and build the query/write pipeline that connects to the existing `MistralEmbeddingClient` (1024-dim). This gives agents the ability to ask "what happened the last N times we saw this pattern?"

## Prerequisite
Session 06-REVISED must be complete. `MistralEmbeddingClient` must exist and be registered.

---

## Task 1 — Database Schema: RAG Signal Memory Tables
Create `atlas/rag/migrations/001_rag_tables.sql`:
- Ensure `pgvector` extension.
- Create `signal_history` table (1024-dim embedding column for Mistral compatibility).
- Create `pattern_memory` table (1024-dim embedding column).
- Create HNSW indexes: `USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 128);`

Create `atlas/rag/migrations/run_migrations.py`:
- Idempotent runner using `asyncpg` directly. Tracks applied migrations.

## Task 2 — RAG Query Layer
Create `atlas/rag/query.py`:
- Implement `RAGQueryEngine` (injected with `MistralEmbeddingClient` and `asyncpg.Pool`).
- `find_similar_contexts`: Searches `signal_history` by cosine similarity.
- `find_regime_outcomes`: Filters by regime/sentiment to calculate historical win rates.
- `find_similar_patterns`: Searches `pattern_memory` for curated lessons.

## Task 3 — Write Pipeline
Create `atlas/rag/writer.py`:
- Implement `RAGWriter` (injected dependencies).
- `write_signal_context`: Builds and embeds text summary of `SignalOutput`. Inserts to `signal_history`.
- `write_outcome`: Enriches existing signals with post-trade PnL data.
- `write_pattern`: Inserts curated lessons to `pattern_memory`.

## Task 4 — Registration and Wiring
Update `atlas/core/registry.py`:
- Run migrations on startup.
- Register `rag_query` and `rag_writer` instances.

## Quality Gates
1. Migrations run successfully without errors.
2. `psql` confirms tables and HNSW indexes exist.
3. `pytest atlas/rag/test_migrations.py`, `test_query.py`, and `test_writer.py` — all pass.
4. No embedding dimension mismatches (`vector(1024)` only).