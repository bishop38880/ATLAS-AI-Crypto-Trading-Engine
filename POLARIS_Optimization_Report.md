# POLARIS Audit & Optimization Report

Generated: 2026-04-28
Audit Mode: GPT-5.5 Read-Only Diagnostic Pass

## Executive Summary

POLARIS is not in a merge-ready state. The strongest health signals are that there are no detected ATLAS -> PROMETHEUS Python imports, no PROMETHEUS -> ATLAS imports after the allowed shared/schema exclusions, no synchronous HTTP library use in the scanned production paths, no `aioredis`/`orjson`/SQLAlchemy/FAISS-style imports in the audited application directories, and the main scorer category weights include one valid `1.0000` sum.

The highest-risk issues are operational rather than theoretical: the full pytest suite fails during collection, PROMETHEUS has schema-version validation but no discovered live `polaris:signals:{asset}` subscriber enforcing `expires_at`, ATLAS contains manual paper-trade routing paths that publish to PROMETHEUS-specific Redis channels outside the documented signal channel, and 58 real Loguru calls pass keyword arguments that Loguru will drop. The repo also has broad timeout debt around Redis and selected HTTP/LLM calls, oversized API handlers, circular imports, and major test coverage gaps in provider, API, and execution-adjacent modules.

Diagnostics ran under `python3` 3.10.12 because `python` and `rg` were not available in this environment. That matters: the target stack is Python 3.12, and one import failure is probably environment-sensitive (`datetime.UTC`).

## Critical Bugs & Correctness Issues (Layer 1 & 2)


| Severity | File                                                | Finding                                                                                                                                                                                                     | Recommended Fix                                                                                                    |
| -------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Critical | `atlas/orchestrator/test_scorer.py`                 | Pytest collection fails on `async async def` at the zero-agents test. The suite stops before execution.                                                                                                     | Replace invalid duplicate `async` token and rerun full suite.                                                      |
| Critical | `atlas/pipeline/test_orchestrator.py`               | Pytest collection fails on `async async def` at latency-budget test.                                                                                                                                        | Replace invalid duplicate `async` token and rerun full suite.                                                      |
| High     | `atlas/shared/telemetry.py`                         | Import health failed: `ImportError: cannot import name 'UTC' from datetime` under Python 3.10.                                                                                                              | Confirm runtime is Python 3.12 everywhere, or replace with `timezone.utc` if 3.10 compatibility is still required. |
| High     | `debug.py`                                          | Import health failed: `Client.__init__() got an unexpected keyword argument 'app'`.                                                                                                                         | Remove/relocate scratch code from import scans or update for current `httpx`/FastAPI test client API.              |
| High     | `atlas/pipeline/llm_router.py`                      | `CancelledError` handler contains `pass` and does not re-raise.                                                                                                                                             | Re-raise after cleanup so shutdown/cancellation semantics remain correct.                                          |
| High     | `atlas/providers/pyth/connector.py`                 | `CancelledError` handler contains `pass` and does not re-raise.                                                                                                                                             | Re-raise `asyncio.CancelledError`; only swallow expected stream errors.                                            |
| Medium   | `atlas/api/_channel_reads.py`, `atlas/api/routes/*` | Multiple `except Exception: pass` patterns in API/channel read paths can silently mask data loss.                                                                                                           | Log with positional Loguru context and return an explicit degraded/empty response.                                 |
| Medium   | `prometheus/backtesting/agent_based_simulator.py`   | `float(snapshot.mid_price)` converts a price to float in backtesting.                                                                                                                                       | Keep prices as `Decimal`; only convert final analytics/ratios when dimensionless.                                  |
| Medium   | Redis keyspace                                      | Static key scan found likely dead writes (`atlas:learning_updates`, `system:kill_switch`, `provider:coinalyze:status`) and ghost reads (`positions:open`, `prometheus:positions`, `polaris:signals:{sym}`). | Normalize key constants and add integration tests proving writer/reader parity.                                    |


## Architectural & Invariant Violations (Layer 2)


| File                                                                       | Violation                                                                                                                                                                                                             | Context Rule Broken                                                                                     |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `atlas/routes/paper_trade.py`                                              | ATLAS exposes `/api/paper-trade`, validates demo-symbol cache, publishes `polaris:paper_trade:{symbol}`, and waits for PROMETHEUS ack. It does not place orders, but it is a second ATLAS -> PROMETHEUS command path. | Context v3 says `polaris:signals:{asset}` is the only permitted ATLAS -> PROMETHEUS communication path. |
| `atlas/routers/executive.py`                                               | Manual executive paper-trade endpoint publishes directly to `polaris:paper_trade:{asset}`.                                                                                                                            | Same hard-wall communication-path invariant.                                                            |
| `atlas/pipeline/position_manager.py`                                       | ATLAS reads PROMETHEUS-owned portfolio/position keys and computes scale-in recommendations from open position state. The file says read-only, but it still couples ATLAS to execution state.                          | ATLAS should recommend from intelligence context only; position tracking belongs to PROMETHEUS.         |
| `atlas/orchestrator/rotation_pipeline.py`                                  | Imports stdlib `json`.                                                                                                                                                                                                | Required serializer is `msgspec`; stdlib `json` is banned in application code.                          |
| `atlas/api/routes/omnibox.py`                                              | Imports stdlib `json` and emits `json.dumps(...)` SSE payloads.                                                                                                                                                       | Required serializer is `msgspec`; stdlib `json` is banned in application code.                          |
| `atlas/rag/pipeline.py`, `atlas/pipeline/*`, `backend/routes/websocket.py` | 58 AST-confirmed Loguru calls use keyword args.                                                                                                                                                                       | Loguru positional formatting only; keyword args are silently discarded.                                 |
| `prometheus/shared/schema_version.py`                                      | Schema-version validation exists, but no live PROMETHEUS `polaris:signals:{asset}` subscriber was discovered in source.                                                                                               | PROMETHEUS must reject stale/unknown signals before execution.                                          |
| `prometheus/`                                                              | No discovered `expires_at`/`is_expired` checks in PROMETHEUS production code.                                                                                                                                         | PROMETHEUS must discard expired ATLAS signals pre-execution.                                            |


## Performance Bottlenecks Identified (Layer 3)


| File/Function                                                                                         | Issue                                                                               | Impact                                                        | Recommendation                                                                                     |
| ----------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `atlas/providers/altfins/adapter.py`                                                                  | Three awaited HTTP calls were found without visible per-call timeout at call site.  | Provider stalls can consume scoring budget.                   | Wrap calls in `asyncio.wait_for` or enforce explicit request timeouts consistently.                |
| `atlas/providers/coingecko/adapter.py`                                                                | Three awaited HTTP calls without visible per-call timeout at call site.             | Market data fetch can hang/degrade cycle latency.             | Use shared `httpx.AsyncClient` timeout plus explicit call-level budget where needed.               |
| `atlas/orchestrator/deepseek_client.py`, `atlas/core/llm_client.py`, `atlas/core/embedding_client.py` | LLM/embedding HTTP calls lack visible call-level timeout checks in diagnostic scan. | Model endpoint latency can block user-facing reasoning paths. | Enforce bounded `wait_for` around each external inference call.                                    |
| `atlas/*`, `backend/*` Redis call sites                                                               | 100 Redis reads/writes/publishes were found without visible timeout wrappers.       | Redis outage can freeze scoring, API, and health endpoints.   | Introduce a small Redis helper with `asyncio.wait_for`, degraded fallback, and consistent logging. |
| `backend/main.py`, `backend/routes/websocket.py`                                                      | `receive_text()` waits without visible timeout.                                     | Dead WebSocket clients can hold handler resources.            | Add heartbeat/idle timeout and cancellation-safe cleanup.                                          |
| `atlas/ml/cqr_calibrator.py`                                                                          | Sync `open(...)` for model read/write.                                              | Blocks event loop if called from async path.                  | Use `aiofiles` or move CPU/file work to `asyncio.to_thread`.                                       |


## Technical Debt & Refactoring Candidates (Layer 4)


| File                                                              | Issue                                                                                             | Complexity Metric                                                                         |
| ----------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `atlas/api/routes/log.py`                                         | Oversized route handler.                                                                          | `get_analyses()` is 134 lines.                                                            |
| `atlas/api/_channel_reads.py`                                     | Multiple oversized channel readers.                                                               | `read_system()` 95 lines, `read_tactical()` 88 lines, `read_scores()` 50 lines.           |
| `atlas/orchestrator/rotation_pipeline.py`                         | Long orchestration function plus stdlib `json`.                                                   | `execute_daily_rotation()` 78 lines.                                                      |
| `atlas/core/registry.py`                                          | Startup registry initialization too large.                                                        | `initialize_registry()` 70 lines.                                                         |
| `atlas/pipeline/llm_router.py`                                    | LLM routing and call code too large.                                                              | `_call_model()` 66 lines; `route_and_call()` 55 lines.                                    |
| `backend/main.py`                                                 | WebSocket push loop too large.                                                                    | `_push_loop()` 54 lines.                                                                  |
| `backend.main` <-> `atlas.signals.outcome_route`                  | Circular import detected.                                                                         | `backend.main -> atlas.signals.outcome_route -> backend.main`.                            |
| `atlas.providers.hydra.listener` <-> `atlas.core.stream_payloads` | Circular import detected.                                                                         | Bidirectional dependency between HYDRA listener and stream payloads.                      |
| `atlas/agents/gnn/temporal/contrastive.py`                        | Multiple candidate dead functions (`augment_all`, `edge_drop`, `feature_mask`, `pretrain`, etc.). | Static reference scan; confirm before deletion because these may be training entrypoints. |


## Test Suite Health (Layer 5)

- **Suite status:** `python3 -m pytest --tb=short -q` fails during collection with 2 syntax errors and 0 executed tests.
- **Collection blockers:** `atlas/orchestrator/test_scorer.py` and `atlas/pipeline/test_orchestrator.py` both contain invalid `async async def`.
- **Untested Core Logic:** 66 source files are in folders without adjacent tests. High-priority gaps include `atlas/providers/coinalyze/provider.py`, `atlas/agents/macro/macro_agent.py`, `atlas/agents/sentiment/sentiment_agent.py`, `atlas/agents/technical/technical_agent.py`, `atlas/agents/synthesiser/synthesiser_agent.py`, `atlas/api/routes/`*, `backend/main.py`, `backend/routes/websocket.py`, `prometheus/services/paper_trade_executor.py`, and `prometheus/backtesting/`*.
- **Test Quality Issues:** `atlas/tests/test_websocket_channels.py` contains `assert True`, which provides no behavioral coverage.
- **Coverage percentage:** Not available because pytest collection fails before coverage can run.

## Integration & Resilience Notes

- Provider validation-gate references are weak in provider code. The scan found mostly direct `.model_validate(...)` calls and cache decode validation, not a clear provider -> `ValidationGate` -> agent path.
- `atlas/orchestrator/scorer.py` produced one `CATEGORY_WEIGHTS` sum of `1.0000` and one scanner match of `0.0000`; the second should be manually inspected as likely a false-positive match or empty/default block.
- Frontend API parity could not be audited in this checkout: no `*.ts`, `*.tsx`, or `package.json` files were found under the workspace.
- Redis resilience handling is uneven. There are localized handlers in RAG/Pyth/chaos utilities, but broad Redis calls lack timeout wrappers.
- PostgreSQL-specific error handling was not found by the resilience scan, despite RAG/PostgreSQL being part of the architecture.
- Circuit-breaker infrastructure exists in `atlas/core/circuit_breaker.py`, but provider HTTP error sites do not consistently show circuit-breaker wrapping at the call site.

## Enhancement Roadmap

Classify each as: PERFORMANCE, RELIABILITY, FEATURE, or QUALITY.

### High-Priority Action Items


| Class       | Effort | Action                                                                                                                                                                 |
| ----------- | ------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| RELIABILITY | S      | Fix the two `async async def` syntax errors and rerun the full pytest suite.                                                                                           |
| RELIABILITY | M      | Add PROMETHEUS pre-execution signal subscriber tests proving schema-version and `expires_at` rejection before any order path.                                          |
| QUALITY     | M      | Replace stdlib `json` usage in `atlas/orchestrator/rotation_pipeline.py` and `atlas/api/routes/omnibox.py` with `msgspec`.                                             |
| RELIABILITY | M      | Convert all 58 Loguru keyword-argument calls to positional formatting so incident context is preserved.                                                                |
| RELIABILITY | M      | Rework `CancelledError` handlers in `atlas/pipeline/llm_router.py` and `atlas/providers/pyth/connector.py` to always re-raise.                                         |
| RELIABILITY | L      | Decide whether ATLAS paper-trade/executive routing is an approved exception. If not, move it behind POLARIS Dashboard -> PROMETHEUS or a dedicated command gateway.    |
| PERFORMANCE | M      | Add a shared timeout wrapper for Redis operations and apply it to scoring, providers, API routes, and circuit-breaker state.                                           |
| RELIABILITY | M      | Establish Redis key constants and writer/reader parity tests for `polaris:signals:{asset}`, paper-trade keys, provider health keys, and portfolio/position state keys. |


### Secondary Refactor Targets


| Class       | Effort | Action                                                                                                                                |
| ----------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------- |
| QUALITY     | M      | Split oversized API/channel handlers into parse, fetch, transform, and response helpers under the 40-line function cap.               |
| QUALITY     | M      | Break circular imports by moving shared payload types out of importer modules and into neutral model modules.                         |
| PERFORMANCE | S      | Review sequential-await candidates and parallelize only independent fetch/write batches.                                              |
| RELIABILITY | M      | Add provider smoke tests for Coinalyze, GitHub, macro/sentiment/technical agents, paper-trade executor, and backend WebSocket routes. |
| QUALITY     | S      | Replace `assert True` with meaningful WebSocket channel assertions.                                                                   |
| RELIABILITY | M      | Add PostgreSQL error handling and explicit degraded fallbacks in RAG persistence paths.                                               |
| QUALITY     | M      | Confirm suspected dead GNN/training functions before removal; ignore FastAPI route false positives.                                   |
| FEATURE     | M      | Restore or add the Vite frontend workspace to this repo if frontend API parity is expected to be audited here.                        |
