# SESSION 06 — Model Stack Configuration: DeepSeek + Mistral Embed + Complexity Router

## Context Files
@atlas/shared/config.py @atlas/core/registry.py @atlas/rag/cache_manager.py @atlas/rag/embedding_service.py

## Prerequisites
Sessions 01–05 complete. Two-tier semantic cache must be operational.

## Goal
Configure the model stack and build the LLM/embedding abstraction layer.
The stack is strictly **DeepSeek (Routine + High-Stakes) + Mistral (Embeddings) + Local stub (Qwen)**.

**ARCHITECTURAL RULE:** xAI / Grok has been **permanently removed** from the stack.
DeepSeek outperformed it in live benchmarks. Do NOT create an `XAIClient`, do NOT
add `xai_*` config fields, do NOT reference Grok anywhere. If you see legacy
references to xAI/Grok in any context file, ignore them — they are stale.

The abstraction layer ensures swapping models later requires changing config values,
not code. When the local model (Qwen2.5-3B via llama.cpp) comes online, set
`LOCAL_ENABLED=true` and routine tasks shift to local inference.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru only.** No `print()`, no stdlib `logging`.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **No hard-coded model names, endpoints, or API keys.** All model references go through `PolarisSettings` / `ModelStackConfig`. If you find a hard-coded model string, it is a bug.
9. **Embedding dimension: 1024.** Mistral Embed produces 1024-dim vectors. If you see references to 384-dim or `all-MiniLM-L6-v2`, those are stale.
10. **Secrets use `pydantic.SecretStr`, not env-var-name strings.** The previous pattern (`api_key_env: str = "MISTRAL_API_KEY"`) stored the *name* of an env var as a config field, which forced downstream code to do `os.getenv(config.api_key_env)` — a direct HW-12 violation. Instead, let `pydantic-settings` resolve the env var directly into a `SecretStr` field. Call `.get_secret_value()` at the API boundary only.

---

## Task 1 — Config Layer: Model Stack Settings

Update `atlas/shared/config.py` via `pydantic-settings`. Use `SecretStr` for all API keys. `pydantic-settings` will read the env vars `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY` etc. automatically when constructed from `ModelStackConfig()`:

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class ModelStackConfig(BaseSettings):
    """Model stack configuration — all LLM and embedding provider settings.

    Every model reference in ATLAS reads from this config.
    Secrets are `SecretStr` — call `.get_secret_value()` only at the
    point of sending the actual HTTP request. Never log SecretStr values.
    """
    model_config = SettingsConfigDict(
        env_prefix="",  # Keys like MISTRAL_API_KEY are read directly
        case_sensitive=True,
    )

    # ─── EMBEDDING PROVIDER ───────────────────────────────
    embed_provider: str = "mistral"
    embed_model: str = "mistral-embed"
    embed_endpoint: str = "https://api.mistral.ai/v1/embeddings"
    embed_api_key: SecretStr = Field(..., alias="MISTRAL_API_KEY")
    embed_dimensions: int = 1024
    embed_batch_size: int = 32
    embed_timeout_s: float = 30.0

    # ─── DEEPSEEK (primary LLM) ──────────────────────────
    deepseek_api_key: SecretStr = Field(..., alias="DEEPSEEK_API_KEY")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_chat_model: str = "deepseek-chat"        # V3 routine
    deepseek_reasoner_model: str = "deepseek-reasoner" # R1 high-stakes
    deepseek_max_tokens: int = 4096
    deepseek_timeout_s: float = 60.0

    # ─── LOCAL MODEL (not yet running) ────────────────────
    local_enabled: bool = False
    local_url: str = "http://localhost:8080"
    local_model_name: str = ""
    local_max_tokens: int = 2048
    local_timeout_s: float = 30.0

    # ─── COMPLEXITY ROUTER ────────────────────────────────
    router_routine_provider: str = "deepseek"
    router_highstakes_provider: str = "deepseek"
    router_fallback_provider: str = "deepseek"
    router_escalation_confluence: int = 140
    router_escalation_on_anomaly: bool = True
```

**NO xAI/Grok fields.** If the original 06-REVISED session had `xai_*` fields, do NOT include them.

**Key-access pattern at the API boundary:**
```python
# CORRECT — SecretStr resolved at the point of use, not passed around as raw str
async def _post_to_deepseek(self, body: dict) -> httpx.Response:
    headers = {"Authorization": f"Bearer {self._config.deepseek_api_key.get_secret_value()}"}
    return await self._client.post(f"{self._config.deepseek_base_url}/chat/completions",
                                   json=body, headers=headers)

# WRONG — never extract the secret into a variable that might be logged/stored
api_key = config.deepseek_api_key.get_secret_value()  # BAD — lost the SecretStr wrapper

# WRONG — previous anti-pattern that this session eliminates
api_key = os.getenv(config.deepseek_api_key_env)  # BAD — HW-12 violation
```

## Task 2 — LLM Client Abstraction

Create `atlas/core/llm_client.py`:

- Abstract class `BaseLLMClient`:
  - `async def complete(prompt: str, max_tokens: int = 1000) -> LLMResponse`
  - `async def health_check() -> bool`
- `LLMResponse` Pydantic model (`frozen=True`): `text: str`, `reasoning: str | None`, `model: str`, `latency_ms: int`, `tokens_used: int`, `cost_estimate_usd: float`

**`DeepSeekClient`** (OpenAI-compatible format):
- Uses `httpx.AsyncClient` for API calls
- Supports both `deepseek-chat` (non-thinking) and `deepseek-reasoner` (thinking mode with `reasoning_content`)
- Reads config from `ModelStackConfig`, never hard-coded
- Accesses the API key via `config.deepseek_api_key.get_secret_value()` at the point of building the request headers — never stores the raw string on the client instance

**`LocalLLMClient`** (llama.cpp stub, inactive until enabled):
- Calls `http://localhost:8080/completion` via httpx
- When `local_enabled=False`, `health_check()` returns `False` and `complete()` raises `LLMProviderError`
- `cost_estimate_usd` always 0.0 for local

**DO NOT** create an `XAIClient`. There is no xAI/Grok integration.

## Task 3 — Embedding Client

Create `atlas/core/embedding_client.py`:

- Class `MistralEmbeddingClient`
- Model: `mistral-embed` (1024 dimensions)
- Accesses `config.embed_api_key.get_secret_value()` at the point of sending each request
- `async def embed(text: str) -> list[float]` — returns 1024 floats
- `async def embed_batch(texts: list[str]) -> list[list[float]]` — batch splitting per `embed_batch_size`
- `async def health_check() -> bool` — embed a test string
- On failure: raise `EmbeddingProviderError`, caller decides fallback
- Wire this into `atlas/rag/embedding_service.py` (from Session 05) to replace the stub

**DIMENSION VERIFICATION:** After building, verify no `vector(384)` exists anywhere:
```bash
grep -rn "vector(384)\|384" atlas/ --include="*.py" --include="*.sql"
# Must return zero results
```

## Task 4 — Complexity Router

Create `atlas/core/complexity_router.py`:

- Class `ComplexityRouter`
- Task classification:
  ```python
  ROUTINE_TASKS = {
      "sentiment_classification",
      "news_headline_scoring",
      "provider_summary_formatting",
      "regime_classification_ohlcv",
      "moderate_conviction_reasoning",
  }
  HIGH_STAKES_TASKS = {
      "conflicting_signal_synthesis",
      "adversarial_trade_review",
      "novel_market_event_analysis",
      "high_conviction_reasoning",
  }
  ```
- Escalation rules (checked in order):
  1. Task type in `HIGH_STAKES_TASKS` → DeepSeek Reasoner
  2. Current conviction score > escalation threshold (from config) → DeepSeek Reasoner
  3. Data has anomaly flags from Validation Gate → DeepSeek Reasoner
  4. `local_enabled=True` → Local model for routine tasks
  5. Otherwise → DeepSeek Chat
- Checks semantic cache (Session 05) BEFORE routing to any model
- Logs every routing decision with structured kwargs

## Task 5 — Integration

- Update `atlas/rag/embedding_service.py` to use `MistralEmbeddingClient` (replacing any stub)
- Register LLM clients in `core/registry.py`
- Update startup health checks to block if DeepSeek or Mistral are offline

## Quality Gates
1. `pytest atlas/core/test_llm_client.py -v` — all pass
   - Test: DeepSeek client formats request correctly with Bearer token
   - Test: DeepSeek client parses `reasoning_content` from reasoner model
   - Test: DeepSeek timeout raises `LLMProviderError` with `retriable=True`
   - Test: Local client returns `False` health check when disabled
   - Test: Logging the DeepSeekClient instance does NOT leak the API key (SecretStr repr)
2. `pytest atlas/core/test_embedding_client.py -v` — all pass
   - Test: Mistral embed returns 1024-dim vector
   - Test: Batch splitting works (100 texts → 4 calls with batch_size=32)
   - Test: Failure raises `EmbeddingProviderError`
3. `pytest atlas/core/test_complexity_router.py -v` — all pass
   - Test: routine task → DeepSeek Chat
   - Test: high-stakes task → DeepSeek Reasoner
   - Test: routine + anomaly flags → escalated to Reasoner
   - Test: cache hit → neither client called
4. `grep -rn "grok\|xai\|x\.ai\|XAIClient\|Grok" atlas/ --include="*.py"` — MUST return zero results
5. `grep -rn "vector(384)\|384\|MiniLM\|sentence.transformers" atlas/ --include="*.py" --include="*.sql"` — MUST return zero results
6. `grep -rn "os.getenv\|os.environ" atlas/core/ atlas/shared/` — MUST return zero results
7. `grep -rn "api_key_env" atlas/` — MUST return zero results (old anti-pattern)
8. `pyright --pythonversion 3.12 atlas/core/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No xAI/Grok references anywhere
- [ ] No `sentence-transformers` or 384-dim references
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings` / `ModelStackConfig`
- [ ] No `api_key_env` string fields — use `SecretStr` with `alias=`
- [ ] No hard-coded model names outside config
- [ ] No hard-coded API keys
- [ ] No `SecretStr.get_secret_value()` called except at the API-request boundary
- [ ] No `print()` — use Loguru
- [ ] All functions ≤ 40 lines
