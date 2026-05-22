# POLARIS — Langfuse Observability Instrumentation
## Antigravity Agent Mode | Single Session | Do not skip steps

---

**Universal Opener — paste this first:**
```
I am building POLARIS, a multi-agent cryptocurrency trading intelligence platform.
@POLARIS_Context_Document_v2.0.md — read this fully before writing a single line of code.

INVARIANTS:
1. ATLAS has ZERO exchange awareness. No CCXT, no order management. Market data IN only.
2. asyncpg ONLY for all database queries. No SQLAlchemy ORM.
3. All I/O is async. No blocking calls.
4. Loguru for all logging. No print(). No stdlib logging.
5. Max 40 lines per function. Type hints on everything. Docstring on everything.
6. Frozen Pydantic models for all data schemas.
7. No os.getenv() — all secrets go through pydantic-settings / PolarisSettings.
8. pytest floor must not decrease. Git commit after session completes.
9. Langfuse is the ONLY observability platform. No Prometheus, no Grafana, no Datadog.
   Do NOT install prometheus_client or any Prometheus-related library.

Confirm you have read the context doc and understood all 9 invariants.
```

---

## OBJECTIVE

Instrument POLARIS with **Langfuse** for full-stack LLM observability. Langfuse was
selected because it is framework-agnostic, has a ClickHouse backend for high-performance
analytics, is self-hostable via Docker Compose, and provides native token/cost tracking
for LLM calls — which directly serves our DeepSeek cost monitoring needs.

This session covers:
1. Langfuse self-hosted deployment via Docker Compose (observability sidecar only — ATLAS runs natively)
2. Langfuse Python SDK integration into the ATLAS telemetry layer
3. Instrumentation of core modules: ComplexityRouter, DeepSeek client, orchestrator pipeline,
   circuit breakers, provider health, and validation gate
4. Cost tracking for DeepSeek chat vs reasoner calls
5. Tests verifying instrumentation does not break the existing pipeline

### WHAT LANGFUSE IS NOT

Langfuse is NOT a replacement for Loguru logging. Loguru continues to handle structured
application logging (debug, info, warning, error). Langfuse captures **traces** — the
end-to-end lifecycle of a reasoning cycle, from data ingestion through agent scoring
to signal output. Think of Loguru as "what happened" and Langfuse as "how the AI
reasoned and what it cost."

### SCOPE BOUNDARIES

**IN SCOPE:**
- Docker Compose file for Langfuse server (web + worker + Postgres + ClickHouse + Redis)
- `atlas/telemetry/langfuse_client.py` — singleton Langfuse client with graceful degradation
- Instrumentation hooks in: DeepSeekClient, ComplexityRouter, orchestrator pipeline,
  circuit_breaker, provider_health, validation_gate
- PolarisSettings additions for Langfuse credentials
- 8 tests

**OUT OF SCOPE:**
- Prometheus, Grafana, or any pull-based metrics system
- Frontend Langfuse integration (Langfuse has its own web UI)
- Langfuse prompt management features (we manage prompts in code)
- OpenTelemetry auto-instrumentation (we use manual instrumentation for precision)

---

## PART 1 — LANGFUSE SELF-HOSTED DEPLOYMENT

### 1A. Docker Compose File

Create `docker/docker-compose.langfuse.yml`:

```yaml
# POLARIS Observability — Langfuse Self-Hosted
# This runs ONLY Langfuse and its dependencies as sidecars.
# ATLAS, FastAPI, Redis (application), and PostgreSQL (application) run natively on the host.
# Langfuse has its OWN Postgres + ClickHouse + Redis — completely isolated from POLARIS data stores.

version: "3.8"

services:
  langfuse-postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: langfuse
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse_local_dev
    volumes:
      - langfuse_pg_data:/var/lib/postgresql/data
    ports:
      - "5433:5432"    # Port 5433 to avoid conflict with POLARIS Postgres on 5432
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U langfuse"]
      interval: 5s
      timeout: 3s
      retries: 5

  langfuse-clickhouse:
    image: clickhouse/clickhouse-server:24.3
    restart: unless-stopped
    environment:
      CLICKHOUSE_DB: langfuse
      CLICKHOUSE_USER: langfuse
      CLICKHOUSE_PASSWORD: langfuse_local_dev
    volumes:
      - langfuse_ch_data:/var/lib/clickhouse
    ports:
      - "8124:8123"    # HTTP interface — offset to avoid conflicts
      - "9001:9000"    # Native interface

  langfuse-redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "6380:6379"    # Port 6380 to avoid conflict with POLARIS Redis on 6379

  langfuse-web:
    image: langfuse/langfuse:3
    restart: unless-stopped
    depends_on:
      langfuse-postgres:
        condition: service_healthy
      langfuse-clickhouse:
        condition: service_started
      langfuse-redis:
        condition: service_started
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse_local_dev@langfuse-postgres:5432/langfuse
      CLICKHOUSE_URL: http://langfuse-clickhouse:8123
      CLICKHOUSE_USER: langfuse
      CLICKHOUSE_PASSWORD: langfuse_local_dev
      REDIS_CONNECTION_STRING: redis://langfuse-redis:6379
      NEXTAUTH_SECRET: polaris-langfuse-dev-secret-change-in-prod
      NEXTAUTH_URL: http://localhost:3001
      SALT: polaris-langfuse-dev-salt-change-in-prod
      LANGFUSE_INIT_ORG_ID: polaris
      LANGFUSE_INIT_ORG_NAME: POLARIS
      LANGFUSE_INIT_PROJECT_ID: polaris-atlas
      LANGFUSE_INIT_PROJECT_NAME: ATLAS Intelligence
      LANGFUSE_INIT_PROJECT_PUBLIC_KEY: pk-lf-polaris-dev
      LANGFUSE_INIT_PROJECT_SECRET_KEY: sk-lf-polaris-dev
      LANGFUSE_INIT_USER_EMAIL: admin@polaris.local
      LANGFUSE_INIT_USER_PASSWORD: polaris-dev-password
      LANGFUSE_INIT_USER_NAME: POLARIS Admin
    ports:
      - "3001:3000"    # Langfuse UI at http://localhost:3001 (POLARIS frontend is on 3000)

  langfuse-worker:
    image: langfuse/langfuse:3
    restart: unless-stopped
    depends_on:
      langfuse-postgres:
        condition: service_healthy
      langfuse-clickhouse:
        condition: service_started
      langfuse-redis:
        condition: service_started
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse_local_dev@langfuse-postgres:5432/langfuse
      CLICKHOUSE_URL: http://langfuse-clickhouse:8123
      CLICKHOUSE_USER: langfuse
      CLICKHOUSE_PASSWORD: langfuse_local_dev
      REDIS_CONNECTION_STRING: redis://langfuse-redis:6379
      SALT: polaris-langfuse-dev-salt-change-in-prod
    command: ["node", "packages/worker/dist/index.js"]

volumes:
  langfuse_pg_data:
  langfuse_ch_data:
```

### 1B. Startup Instructions (add to README or docs/)

```
# Start Langfuse observability stack (one-time setup):
cd docker
docker compose -f docker-compose.langfuse.yml up -d

# Access Langfuse UI:
# http://localhost:3001
# Login: admin@polaris.local / polaris-dev-password

# Langfuse API endpoint (for ATLAS SDK):
# http://localhost:3001

# Stop:
docker compose -f docker-compose.langfuse.yml down

# Reset all data:
docker compose -f docker-compose.langfuse.yml down -v
```

---

## PART 2 — LANGFUSE CLIENT SINGLETON

Create `atlas/telemetry/__init__.py` (empty package init).

Create `atlas/telemetry/langfuse_client.py`:

```python
"""
Langfuse observability client for POLARIS.

Provides a singleton Langfuse instance that all instrumented modules import.
If Langfuse is unreachable or disabled, all calls silently no-op — the SDK
is designed to never break the host application.

Usage:
    from atlas.telemetry.langfuse_client import get_langfuse
    langfuse = get_langfuse()
    trace = langfuse.trace(name="analysis_cycle", ...)
"""

from __future__ import annotations

import functools
from langfuse import Langfuse
from loguru import logger

_langfuse_instance: Langfuse | None = None


def get_langfuse() -> Langfuse | None:
    """Return the singleton Langfuse client, or None if disabled/unavailable.

    Reads config from environment variables (set via PolarisSettings):
      LANGFUSE_PUBLIC_KEY
      LANGFUSE_SECRET_KEY
      LANGFUSE_HOST

    If any are missing or connection fails, returns None.
    All instrumentation code must handle None gracefully.
    """
    global _langfuse_instance
    if _langfuse_instance is not None:
        return _langfuse_instance

    try:
        from atlas.shared.config import polaris_settings

        if not polaris_settings.langfuse_enabled:
            logger.info("Langfuse disabled via config — observability traces will not be collected")
            return None

        _langfuse_instance = Langfuse(
            public_key=polaris_settings.langfuse_public_key,
            secret_key=polaris_settings.langfuse_secret_key,
            host=polaris_settings.langfuse_host,
        )

        if _langfuse_instance.auth_check():
            logger.info(f"Langfuse connected: {polaris_settings.langfuse_host}")
        else:
            logger.warning("Langfuse auth_check failed — traces will be queued but may not arrive")

        return _langfuse_instance

    except Exception as e:
        logger.warning(f"Langfuse init failed: {e} — running without observability")
        return None


def shutdown_langfuse() -> None:
    """Flush all pending events to Langfuse. Call on application shutdown."""
    global _langfuse_instance
    if _langfuse_instance is not None:
        try:
            _langfuse_instance.flush()
            logger.info("Langfuse flushed successfully")
        except Exception as e:
            logger.warning(f"Langfuse flush failed: {e}")
        _langfuse_instance = None
```

---

## PART 3 — CONFIG ADDITIONS

Add to `PolarisSettings` (wherever pydantic-settings lives — `atlas/shared/config.py` or `atlas/settings.py`):

```python
# Langfuse observability
langfuse_enabled: bool = True
langfuse_public_key: str = "pk-lf-polaris-dev"
langfuse_secret_key: str = "sk-lf-polaris-dev"
langfuse_host: str = "http://localhost:3001"
```

Add to `.env.example`:

```env
# ─── Langfuse Observability ────────────────────────────────────
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=pk-lf-polaris-dev
LANGFUSE_SECRET_KEY=sk-lf-polaris-dev
LANGFUSE_HOST=http://localhost:3001
```

---

## PART 4 — INSTRUMENTATION: DeepSeek LLM CLIENT

This is the highest-value instrumentation point. Every LLM call gets tracked with
model, tokens, cost, and latency.

**File to modify:** `atlas/core/llm_client.py` (DeepSeekClient.complete method)

Add a Langfuse `generation` span wrapping the httpx call:

```python
async def complete(self, prompt, system_prompt=None, max_tokens=1000,
                   temperature=0.1, use_reasoner=False,
                   trace=None, parent_observation=None) -> LLMResponse:
    """
    ... existing docstring ...

    New optional params:
        trace: Active Langfuse trace to attach this generation to.
        parent_observation: Parent span (e.g., the agent that triggered this call).
    """
    model_name = self._config.deepseek_reasoner_model if use_reasoner else self._config.deepseek_chat_model
    start_ms = time.monotonic_ns()

    # Create Langfuse generation span if trace is available
    generation = None
    if trace is not None:
        generation = trace.generation(
            name=f"deepseek_{model_name}",
            model=model_name,
            input={"system": system_prompt or "", "user": prompt[:500]},  # Truncate for storage
            model_parameters={"temperature": temperature, "max_tokens": max_tokens},
            metadata={"use_reasoner": use_reasoner},
        )

    # ... existing httpx POST logic ...

    # After successful response:
    if generation is not None:
        generation.end(
            output=response_text[:500],  # Truncate for storage
            usage={
                "input": tokens_in,
                "output": tokens_out,
                "total": tokens_in + tokens_out,
                "unit": "TOKENS",
                "input_cost": tokens_in * input_cost_per_token,
                "output_cost": tokens_out * output_cost_per_token,
                "total_cost": (tokens_in * input_cost_per_token) + (tokens_out * output_cost_per_token),
            },
            level="DEFAULT",
        )

    # Cost rates (from Session 06):
    # deepseek-chat:     $0.27/1M input, $1.10/1M output
    # deepseek-reasoner: $0.55/1M input, $2.19/1M output

    return llm_response
```

**CRITICAL:** The `trace` and `parent_observation` params are optional with default `None`.
Existing callers that don't pass them continue to work unchanged. Zero breaking changes.

---

## PART 5 — INSTRUMENTATION: COMPLEXITY ROUTER

**File to modify:** `atlas/core/complexity_router.py` (ComplexityRouter.route method)

The router creates the **trace** for each reasoning task. This trace becomes the parent
for all downstream LLM generations.

```python
async def route(self, task_type, prompt, system_prompt=None, ...) -> LLMResponse:
    """... existing docstring ..."""

    langfuse = get_langfuse()
    trace = None
    if langfuse is not None:
        trace = langfuse.trace(
            name=f"reasoning_{task_type}",
            metadata={
                "task_type": task_type,
                "tier": tier.value,
                "provider": selected_provider.provider_name,
                "confluence": current_confluence,
                "has_anomaly": has_anomaly_flags,
            },
            tags=["atlas", tier.value, selected_provider.provider_name],
        )

    # ... existing routing logic ...

    # Pass trace to the selected provider's complete() call:
    response = await selected_provider.complete(
        prompt=prompt,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        trace=trace,          # NEW — Langfuse trace propagation
    )

    # Score the trace with the routing decision
    if trace is not None:
        trace.score(name="route_decision", value=1.0 if tier == TaskTier.HIGH_STAKES else 0.0)

    return response
```

---

## PART 6 — INSTRUMENTATION: ORCHESTRATOR PIPELINE

**File to modify:** `pipeline/orchestrator.py` (or wherever the 30-minute analysis cycle runs)

Each analysis cycle gets a top-level Langfuse trace. Agent evaluations become spans
within that trace.

```python
async def run_analysis_cycle(self, asset: str) -> SignalOutput:
    """Run the complete analysis cycle for one asset."""

    langfuse = get_langfuse()
    trace = None
    if langfuse is not None:
        trace = langfuse.trace(
            name="analysis_cycle",
            input={"asset": asset},
            metadata={"rotation_size": len(self._active_rotation)},
            tags=["atlas", "cycle", asset],
        )

    cycle_start = time.monotonic()

    # ... existing agent dispatch loop ...
    for agent in self._agents:
        agent_span = None
        if trace is not None:
            agent_span = trace.span(
                name=f"agent_{agent.name}",
                metadata={"category": agent.category, "max_points": agent.max_points},
            )

        result = await agent.evaluate(context)

        if agent_span is not None:
            agent_span.end(
                output={
                    "score": result.score,
                    "max": agent.max_points,
                    "direction": result.direction,
                },
                level="DEFAULT" if result.score > 0 else "WARNING",
            )

    # ... existing scoring, risk gate, signal output ...

    cycle_duration_ms = (time.monotonic() - cycle_start) * 1000

    if trace is not None:
        trace.update(
            output={
                "total_score": signal.total_score,
                "decision": signal.decision,
                "conviction": signal.conviction,
                "passes_gate": signal.passes_gate,
            },
            metadata={"cycle_duration_ms": round(cycle_duration_ms)},
        )
        trace.score(name="conviction", value=signal.conviction / 100.0)
        trace.score(name="passes_gate", value=1.0 if signal.passes_gate else 0.0)

    return signal
```

---

## PART 7 — INSTRUMENTATION: INFRASTRUCTURE MODULES

These are lightweight — just emit events, not full traces.

### 7A. Circuit Breaker — `atlas/core/circuit_breaker.py`

On state transitions (CLOSED → OPEN, OPEN → HALF_OPEN, HALF_OPEN → CLOSED):

```python
langfuse = get_langfuse()
if langfuse is not None:
    langfuse.event(
        name="circuit_breaker_transition",
        metadata={
            "provider": self._provider_name,
            "from_state": old_state,
            "to_state": new_state,
            "failure_count": self._failure_count,
        },
        level="WARNING" if new_state == "OPEN" else "DEFAULT",
    )
```

### 7B. Provider Health — `atlas/core/provider_health.py`

On health check results:

```python
langfuse = get_langfuse()
if langfuse is not None:
    langfuse.event(
        name="provider_health_check",
        metadata={
            "provider": provider_name,
            "status": status,  # "healthy" | "degraded" | "disconnected"
            "latency_ms": round(latency_ms),
            "tier": tier,
        },
        level="WARNING" if status != "healthy" else "DEFAULT",
    )
```

### 7C. Validation Gate — `atlas/core/validation_gate.py`

On validation outcomes (especially anomaly detections):

```python
langfuse = get_langfuse()
if langfuse is not None:
    langfuse.event(
        name="validation_gate",
        metadata={
            "asset": asset,
            "provider": provider_name,
            "result": "pass" | "fail" | "anomaly",
            "reason": reason_string,
        },
        level="ERROR" if result == "fail" else ("WARNING" if result == "anomaly" else "DEFAULT"),
    )
```

---

## PART 8 — FASTAPI LIFECYCLE INTEGRATION

**File to modify:** `backend/main.py`

```python
from atlas.telemetry.langfuse_client import get_langfuse, shutdown_langfuse

# In the lifespan or startup event:
langfuse = get_langfuse()  # Initialises singleton, logs connection status

# In the lifespan shutdown or shutdown event:
shutdown_langfuse()  # Flushes all pending events before process exits
```

That's it for main.py. No `/metrics` endpoint, no middleware, no Prometheus.

---

## PART 9 — DEPENDENCIES

**File to modify:** `pyproject.toml`

Add to dependencies:

```toml
"langfuse>=3.0",
```

Do NOT add: `prometheus_client`, `prometheus-fastapi-instrumentator`, or any Prometheus library.

---

## PART 10 — TESTS

File: `atlas/telemetry/test_langfuse_integration.py`

All tests use mocking — they do NOT require a running Langfuse server.

```
1. test_get_langfuse_returns_none_when_disabled
   - Set langfuse_enabled=False in settings
   - Assert get_langfuse() returns None

2. test_get_langfuse_returns_singleton
   - Mock Langfuse constructor
   - Call get_langfuse() twice
   - Assert constructor called only once (singleton)

3. test_shutdown_flushes
   - Mock Langfuse.flush()
   - Call shutdown_langfuse()
   - Assert flush was called

4. test_deepseek_client_works_without_trace
   - Call DeepSeekClient.complete() with trace=None
   - Assert it returns LLMResponse normally (no Langfuse dependency)

5. test_deepseek_client_creates_generation_with_trace
   - Mock a Langfuse trace object
   - Call DeepSeekClient.complete() with trace=mock_trace
   - Assert trace.generation() was called with correct model name

6. test_circuit_breaker_emits_event_on_trip
   - Mock get_langfuse() to return a mock Langfuse instance
   - Trip the circuit breaker
   - Assert langfuse.event() was called with level="WARNING"

7. test_validation_gate_emits_event_on_anomaly
   - Mock get_langfuse()
   - Trigger an anomaly detection in the validation gate
   - Assert langfuse.event() was called with result="anomaly"

8. test_instrumentation_does_not_break_on_langfuse_error
   - Mock get_langfuse() to return a mock that throws on .trace()
   - Run a reasoning task through the ComplexityRouter
   - Assert the router still returns a valid LLMResponse (Langfuse errors are swallowed)
```

---

## QUALITY GATES

Before marking this session complete:

1. All 8 new tests pass
2. pytest floor has NOT decreased — `python -m pytest -p no:randomly -x` passes
3. No `prometheus_client` or `prometheus` imports anywhere: `grep -r "prometheus" atlas/ backend/ --include="*.py"`
4. No `os.getenv()` in new code
5. Docker Compose starts cleanly: `docker compose -f docker/docker-compose.langfuse.yml up -d`
6. Langfuse UI accessible at `http://localhost:3001`
7. When ATLAS runs a cycle with Langfuse up, traces appear in the Langfuse UI
8. When Langfuse is down, ATLAS runs normally with zero errors (graceful degradation)
9. `git add -A && git commit -m "feat: langfuse-observability — instrument ATLAS with Langfuse tracing + self-hosted deployment"`

---

## DIRECTORY STRUCTURE (new/modified files)

```
docker/
  docker-compose.langfuse.yml         # NEW — Langfuse + ClickHouse + Postgres + Redis sidecar

atlas/
  telemetry/
    __init__.py                        # NEW — package init
    langfuse_client.py                 # NEW — singleton client + shutdown helper
    test_langfuse_integration.py       # NEW — 8 tests
  core/
    llm_client.py                      # MODIFIED — add trace/generation params to complete()
    complexity_router.py               # MODIFIED — create trace per reasoning task
    circuit_breaker.py                 # MODIFIED — emit event on state transitions
    provider_health.py                 # MODIFIED — emit event on health checks
    validation_gate.py                 # MODIFIED — emit event on anomalies
  shared/
    config.py                          # MODIFIED — add langfuse_* settings

pipeline/
  orchestrator.py                      # MODIFIED — create trace per analysis cycle

backend/
  main.py                              # MODIFIED — init + shutdown Langfuse in lifespan

pyproject.toml                         # MODIFIED — add langfuse>=3.0
.env.example                           # MODIFIED — add LANGFUSE_* vars
```

---

## NOTES FOR THE AGENT

1. **Every instrumentation call is wrapped in a None-check.** If `get_langfuse()` returns
   None or if `trace` is None, the code path must be identical to the un-instrumented path.
   Langfuse is additive observability — it must NEVER affect control flow or data output.

2. **Langfuse SDK is fully async internally.** It uses a background worker thread and an
   internal queue. Calls to `trace()`, `generation()`, `span()`, `event()`, and `score()`
   return immediately. They add near-zero latency to the hot path.

3. **The SDK catches its own exceptions.** Even if Langfuse server is unreachable, the SDK
   will not throw. But we add our own None-checks for defense-in-depth.

4. **Do NOT install prometheus_client.** If you see any existing `prometheus_client` imports
   in the codebase, leave them alone (they may be from a previous session). Do not add new ones.

5. **Langfuse v3 requires ClickHouse.** The Docker Compose includes ClickHouse because
   Langfuse v3 (which we pin with `langfuse/langfuse:3`) uses it as its OLAP backend.
   This is entirely isolated from POLARIS — ClickHouse runs inside Docker only.

6. **Port assignments are intentionally offset:**
   - Langfuse UI: 3001 (POLARIS frontend is 3000)
   - Langfuse Postgres: 5433 (POLARIS Postgres is 5432)
   - Langfuse Redis: 6380 (POLARIS Redis is 6379)
   - ClickHouse HTTP: 8124 (ATLAS FastAPI is 8000)

7. **The LANGFUSE_INIT_* environment variables** auto-create the organization, project,
   and admin user on first boot. After first boot, they are ignored. This means
   `docker compose down -v && docker compose up -d` gives you a clean Langfuse instance.

8. **Cost tracking is the killer feature here.** Every DeepSeek call logs input/output
   tokens and cost. Over time, the Langfuse dashboard shows cumulative LLM spend per
   agent, per task tier, per asset. This directly informs the decision of when to move
   routine tasks to the local model (Transition 1 in Session 06).

9. **Truncate prompt/response to 500 chars in Langfuse.** Full prompts can be 4000+ tokens.
   Langfuse stores everything, and at 33 assets × 11 agents × 30-minute cycles, storage
   grows fast. 500 chars gives enough context for debugging without exploding ClickHouse.
