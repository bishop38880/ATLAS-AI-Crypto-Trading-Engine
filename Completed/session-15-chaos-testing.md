# SESSION 15 — Chaos Engineering: Automated Fault Injection Testing

## Context Files
@atlas/core/circuit_breaker.py @atlas/core/provider_health.py @atlas/core/validation_gate.py @pipeline/hierarchical_orchestrator.py @atlas/core/partial_results.py

## Prerequisites
Session 13 (FINCON hierarchy) must be complete. Session 04 (Partial Results) defines
the quorum threshold (`QUORUM_MIN_AGENTS=3`) referenced in this session.

## Goal
Build a chaos testing harness that systematically verifies ATLAS degrades gracefully
under every failure mode. Tests target the FINCON 8-agent 3-tier hierarchy (5 Tier-1
analysts + Risk Agent + Portfolio Agent + Signal Synthesiser) and the local HYDRA
stream — not legacy components.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` → `msgspec`.
   `pandas`/`SQLAlchemy`/`pickle`/`joblib` → banned.
3. **`PolarisSettings` only.**
4. **40-line function limit.**
5. **Loguru only.** No f-strings in loggers.
6. **Test floor is sacred.**
7. **FINCON architecture.** Tests target the 8-agent 3-tier hierarchy:
   - Tier 1: 5 Analyst agents (Technical, Derivatives, OnChain, Sentiment, MarketRegime)
   - Tier 2: Risk Agent (veto authority) + Portfolio Agent
   - Tier 3: Signal Synthesiser
8. **Risk Agent veto semantics on timeout:** If the Risk Agent times out (exceeds
   its tier deadline), the orchestrator MUST treat this as an IMPLICIT VETO — the
   final signal is `decision=NO_POSITION`. The Risk Agent's 35% weight is NOT
   silently dropped. This is the safe default — missing risk analysis → no trade.
9. **HYDRA injector targets Streams (post-Session 18) with Pub/Sub fallback.**
   If the codebase is still on Pub/Sub, the injector targets Pub/Sub. Tests must
   pass on both topologies.

---

## Task 1 — Chaos Experiment Framework

Create `atlas/testing/chaos/framework.py`:

### ChaosExperiment Model

```python
from typing import Literal
from pydantic import BaseModel, Field


class ChaosExperiment(BaseModel, frozen=True):
    name: str
    fault_type: Literal[
        "provider_down", "hydra_stream_death", "latency_spike",
        "corrupt_data", "agent_timeout", "redis_down", "cascade_failure",
    ]
    target: str                           # e.g. "coinalyze", "risk_agent"
    duration_seconds: float = Field(gt=0, le=300)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)
```

### ChaosResult Model

```python
class ChaosResult(BaseModel, frozen=True):
    experiment_name: str
    system_crashed: bool                  # MUST be False for every experiment
    cycles_during_fault: int
    cycles_after_recovery: int
    avg_confidence_during_fault: float
    avg_confidence_after_recovery: float
    circuit_breakers_opened: list[str]    # provider names
    provider_health_degraded: list[str]   # provider names marked DEGRADED
    recovery_time_seconds: float | None   # None if recovery not observed
    unhandled_exceptions: list[str]       # stack traces, MUST be empty
    final_verdict: Literal["PASS", "FAIL"]
```

### ChaosRunner

```python
class ChaosRunner:
    async def run_experiment(self, experiment: ChaosExperiment) -> ChaosResult:
        """Inject fault → N analysis cycles → remove fault → N recovery cycles."""
        ...
```

Sequence:
1. Snapshot baseline confidence over N=10 cycles
2. Inject fault
3. Run N=20 cycles under fault, collect metrics
4. Remove fault
5. Run N=20 recovery cycles, collect metrics
6. Assemble `ChaosResult`

## Task 2 — Fault Injectors

Create `atlas/testing/chaos/injectors.py`:

- `ProviderDownInjector`: Forces circuit breaker OPEN for target HTTP provider
  (Coinalyze, DeFi Llama, Pyth, FRED, Messari, HyperTracker).
- `HydraStreamDeathInjector`: Stops publishing to HYDRA ingestion channel/stream.
  Auto-detects Pub/Sub vs. Streams topology at runtime.
- `LatencySpikeInjector`: Adds configurable delay to target provider.
- CorruptDataInjector: Corrupts a field (e.g., injecting Decimal("0") for price to test mathematical graceful degradation without triggering a Pydantic schema rejection, injecting a malformed msgspec payload, or injecting an invalid enum value).
- `AgentTimeoutInjector`: Makes target agent's `score()` sleep past its tier deadline.
- `RedisDownInjector`: Replaces Redis connection with one that raises
  `redis.asyncio.ConnectionError`.

Each injector exposes `async inject() -> None` and `async remove() -> None`.

## Task 3 — Pre-Built Experiment Suite

Create `atlas/testing/chaos/experiments.py` — 8 standard experiments:

1. **`single_provider_down`** — Kill Coinalyze. Expected: system continues,
   Derivatives agent marks DEGRADED, confidence drops, circuit breaker OPEN.

2. **`hydra_stream_disconnect`** — Kill HYDRA feed. Expected:
   `HydraStreamListener` marks DEGRADED, Liquidation agent returns safe neutral
   score, no crash.

3. **`latency_spike_defillama`** — Add 500ms to DeFi Llama. Expected:
   Session 04 partial-results machinery discards the late response, pipeline
   continues with available evidence.

4. **`corrupt_hydra_matrix`** — Send garbage cascade payload. Expected:
   Validation Gate rejects, HYDRA marked DEGRADED, zero impact on score.

5. **`tier1_analysts_timeout_2_of_5`** — Timeout 2 of 5 Tier-1 analysts.
   Expected: quorum preserved (3 ≥ `QUORUM_MIN_AGENTS=3`), partial score emitted
   with confidence penalty, signal proceeds with reduced conviction.

6. **`tier1_analysts_timeout_3_of_5`** — Timeout 3 of 5 Tier-1 analysts.
   Expected: quorum BREACHED (2 < 3), orchestrator emits `NO_POSITION` signal.

7. tier2_risk_timeout — Timeout Risk Agent. Expected: IMPLICIT VETO
(per Invariant 8) — orchestrator emits NO_POSITION with
deepseek_evaluation.key_risks=["RISK_AGENT_TIMEOUT_VETO"]. CRUCIAL REMINDER: Because DeepSeekDecision and SignalOutput models are frozen=True, the orchestrator MUST construct a new safe-fallback signal object during the timeout catch, rather than attempting to mutate an existing one. Risk Agent's 35% weight is NOT silently dropped.

8. **`redis_flap`** — Redis down for 5s, then back. Expected: agents mark
   DEGRADED, reconnection succeeds within 10s of Redis recovery, cycles resume.

9. **`cascade_failure`** — Coinalyze fails, then DeFi Llama fails 5s later.
   Expected: two circuit breakers OPEN independently, degradation compounds
   but system stays up.

## Task 4 — Assertions and Reporting

Per experiment, assert:
- `system_crashed == False` (MUST pass for all — fails the whole suite otherwise).
- Signals during fault have strictly lower confidence than baseline.
- Provider health scores reflect degradation.
- Circuit breakers opened when expected.
- Recovery within 60s after fault removal (where applicable).
- `unhandled_exceptions == []` (empty).

Generate markdown report at `atlas/testing/chaos/report.md` summarizing all 9
experiments: name, verdict, key metrics, recovery time.

## Quality Gates
1. `pytest atlas/testing/chaos/test_chaos.py -v` — all 9 experiments pass.
2. No experiment causes a system crash or unhandled exception.
3. `pyright --pythonversion 3.12 atlas/testing/` — zero errors.
4. `grep -rn "CoinGlass\|coinglass" atlas/testing/chaos/` — zero (Coinalyze only).
5. `grep -rn "10-agent\|ten agent" atlas/testing/chaos/` — zero (FINCON 8-agent only).

## Anti-Pattern Checklist
- [ ] No references to legacy 10-agent flat architecture
- [ ] No CoinGlass — Coinalyze is the derivatives provider
- [ ] HYDRA injector handles both Pub/Sub and Streams topologies
- [ ] `QUORUM_MIN_AGENTS=3` referenced (not hardcoded per-experiment)
- [ ] Risk Agent timeout → implicit veto (`NO_POSITION`), not silent drop
- [ ] `ChaosResult` model fully defined with `final_verdict` Literal
- [ ] No `import aioredis` — `redis.asyncio`
- [ ] No `import json` — `msgspec`
- [ ] All functions ≤ 40 lines
