# SESSION 04 — Event-Driven Orchestrator: Partial Results + Fast-Path Risk Veto

## Context Files
@pipeline/orchestrator.py @agents/base.py @pipeline/scorer.py @atlas/core/registry.py @atlas/providers/hydra/listener.py

## Prerequisites
Sessions 01–03 complete. The HYDRA listener scaffold (`atlas/providers/hydra/listener.py`) must be in place, along with the other provider scaffolding (flat standalone classes — NOT inherited from any `BaseProvider` base class, which does not exist in this codebase).

## Goal
Replace the current `ThreadPoolExecutor` + `as_completed` pattern in
`PipelineOrchestrator.run()` with async-native execution that supports
partial results and a fast-path risk veto. If 8 of 10 agents respond
within 80ms, score with those 8 and zero-score the 2 stragglers.

**CRITICAL UPGRADE:** The orchestrator must monitor the HYDRA data buffer.
The Risk Agent gets a "Fast-Path" — if a severe market event is unfolding
via the local HYDRA stream, the Risk Agent evaluates first and can
short-circuit the entire orchestrator, returning a "No Position" veto
instantly without waiting for the other agents to finish their 80ms budget.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers.
5. **Loguru only.** No `print()`, no stdlib `logging`. No f-strings in loggers.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **`Decimal` for financial fields** in `ActionBlock`. Scores use `int`/`float`.
9. **Polars IS approved for time-series dataframes.** This session's orchestrator signature uses `pl.LazyFrame` — that is intentional. Pandas is banned; Polars is the sanctioned dataframe library. See the updated `050-tech-stack.mdc`.
10. **No `BaseProvider` base class.** The HYDRA listener and other providers are standalone classes. Do not introduce a `BaseProvider` abstract base class anywhere.

---

## Task 1 — Convert Orchestrator to Async & Partial Results

Refactor `PipelineOrchestrator`:

- Change `def run(...)` to `async def run(...)`
- Change `_safe_score` to `async def _safe_score(...)`
- Replace `ThreadPoolExecutor` with `asyncio.wait()` for the general agent pool

```python
async def run(self, data: pl.LazyFrame, context: dict | None = None, ...) -> SignalOutput:
    # Build risk context from Redis + HYDRA buffer
    risk_context = await self._build_risk_context()
    context = {**(context or {}), **risk_context}

    # Dispatch all agents
    tasks = {
        asyncio.create_task(
            self._safe_score(agent, data, context),
            name=agent.name,
        ): agent
        for agent in self._agents
    }

    done, pending = await asyncio.wait(
        tasks.keys(),
        timeout=0.08,  # 80ms budget
        return_when=asyncio.ALL_COMPLETED,
    )

    results: list[AgentResult] = []
    for task in done:
        results.append(task.result())
    for task in pending:
        agent = tasks[task]
        task.cancel()
        logger.warning("Agent timed out", agent=agent.name, budget_ms=80)
        results.append(agent._make_zero_result(reason="Timed out (80ms budget)"))

    return self._scorer.score(agent_results=results, ...)
```

## Task 2 — Make BaseAgent.score Async

Update `BaseAgent`:
- Change abstract method `def score(...)` to `async def score(...)`
- Update all existing agent implementations to `async def score(...)`
- Agents doing pure CPU work can be async functions returning immediately

## Task 3 — Fast-Path Risk Veto

**CRITICAL LOGIC:** The naive `asyncio.wait(..., return_when=ALL_COMPLETED)` waits
the full 80ms even if the Risk Agent returns a veto in 2ms. This defeats the purpose
of a fast-path.

**Implementation:** Use `asyncio.as_completed()` or task polling to intercept the
Risk Agent's result immediately:

```python
async def run(self, data: pl.LazyFrame, context: dict | None = None, ...) -> SignalOutput:
    risk_context = await self._build_risk_context()
    context = {**(context or {}), **risk_context}

    # Dispatch Risk Agent separately for fast-path
    risk_agent = self._get_risk_agent()
    risk_task = asyncio.create_task(
        self._safe_score(risk_agent, data, context),
        name="risk_agent_fast_path",
    )

    # Dispatch remaining agents
    other_tasks = {
        asyncio.create_task(
            self._safe_score(agent, data, context),
            name=agent.name,
        ): agent
        for agent in self._agents if agent.name != risk_agent.name
    }

    # Wait for Risk Agent first
    risk_result = await risk_task

    # FAST-PATH: If Risk vetoes, cancel everything and return immediately
    if risk_result.veto:
        for task in other_tasks:
            task.cancel()
        logger.warning("Fast-path veto triggered", reasons=risk_result.veto_reasons)
        return self._scorer.build_vetoed_signal(risk_result)

    # Otherwise, wait for remaining agents with budget
    done, pending = await asyncio.wait(
        other_tasks.keys(),
        timeout=0.08,
        return_when=asyncio.ALL_COMPLETED,
    )

    results = [risk_result]
    results.extend(task.result() for task in done)
    for task in pending:
        agent = other_tasks[task]
        task.cancel()
        results.append(agent._make_zero_result(reason="Timed out"))

    return self._scorer.score(agent_results=results, ...)
```

## Task 4 — Quorum Check

Add quorum enforcement:
- Minimum 3 of 5 agent categories (TECHNICAL, DERIVATIVES, ONCHAIN, SENTIMENT, RISK) must have at least one responding agent
- If fewer than 3 categories respond, log CRITICAL and return a zero-confidence signal with `decision: "No Position"`
- Add `QUORUM_MIN_CATEGORIES = 3` to config (via `PolarisSettings`)

## Task 5 — Latency Telemetry

Add per-agent latency tracking:
- Wrap each `_safe_score` call with `time.perf_counter_ns()`
- Record latency in `AgentResult.telemetry.latency_ms`
- Log a summary after each cycle: agent name, latency, timed_out (bool)

## Quality Gates
1. `pytest pipeline/test_orchestrator.py -v` — all pass
   - Test: all agents respond within 80ms → full results
   - Test: one agent sleeps 200ms → gets zero-scored, others scored normally
   - Test: quorum failure (only 2 categories) → No Position signal
   - Test: Risk veto with Tier-4 HYDRA cascade → orchestrator cancels pending agents and returns veto in < 5ms
   - Test: Risk Agent no-veto → normal 80ms flow proceeds
2. Verify `async def` throughout — `grep -r "def score" agents/` shows only `async def score`
3. `pyright --pythonversion 3.12 pipeline/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `ThreadPoolExecutor` — must use `asyncio.wait()` / `asyncio.as_completed()`
- [ ] No `print()` — use Loguru
- [ ] No f-strings in logger calls
- [ ] No `BaseProvider` abstract base class introduced
- [ ] Fast-path veto does NOT use `ALL_COMPLETED` — must short-circuit
- [ ] All functions ≤ 40 lines
