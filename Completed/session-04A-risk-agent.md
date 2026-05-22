# SESSION 04A — Risk & Portfolio Management Agent (Safety Layer, Veto Authority)

## Context Files
@agents/base.py @pipeline/orchestrator.py @pipeline/scorer.py @atlas/config.py @atlas/models/signal.py @atlas/providers/hydra/listener.py

## Prerequisites
Session 04 complete. Async orchestrator with fast-path veto must be operational.
The HYDRA stream listener must be running (standalone scaffold — see Session 04 for the no-`BaseProvider` convention).

## Goal
Build the Safety Layer — the single most important agent in ATLAS. The Risk &
Portfolio Management agent has ABSOLUTE VETO POWER. If this agent vetoes, the
final decision is No Position regardless of all other signals. Without this
agent, the system can send high-conviction buy signals during a flash crash
with 100% portfolio exposure. This session makes that impossible.

---

## CRITICAL ARCHITECTURAL DECISION — Risk Agent is VETO-ONLY (not weighted)

Earlier versions of this session claimed the Risk agent had "35% weight" and
`max_points=77`. That was **wrong** and produced a weight-math contradiction:
the Session 09B CATEGORY_WEIGHTS table lists 10 other categories summing to
1.00 without Risk, so Risk-plus-10-others would sum to 1.35 (135%).

The correct architecture separates concerns cleanly:
- **Scoring agents** (10 categories from Session 09B) produce the confluence
  score. Their weights sum to 1.00 = 220 points.
- **Risk agent** is a pure gate. It contributes ZERO points to the confluence
  score. Its sole output is a binary `veto: bool` flag. When `veto=True`, the
  scorer short-circuits to `decision=No Position` regardless of the confluence
  score. When `veto=False`, the scorer proceeds normally using the 10-category
  weighted sum.

This means `max_points=0` for the Risk agent. It is NOT a weighted participant
in the score. Any "35% weight" language anywhere (including Session 13's
hierarchical restatement) is legacy and must be removed.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.** Extract helpers — each risk dimension should be its own private method.
5. **Loguru only.** No `print()`, no stdlib `logging`. Structured kwargs in all log calls.
6. **ATLAS has ZERO exchange awareness.** The Risk Agent reads portfolio state from Redis (populated by PROMETHEUS). It never queries any exchange.
7. **Test floor is sacred.**
8. **`Decimal` for all financial math.** Drawdown percentages, exposure, position sizes — all `Decimal`.
9. **HYDRA data is local.** Read cascade events from the `HydraStreamListener` buffer. No external HTTP calls for liquidation data.
10. **Risk agent is VETO-ONLY.** `max_points=0`. It does NOT contribute to the confluence score.
11. **`redis.asyncio.Redis.get()` returns `bytes | None`.** Decoding to `Decimal` requires an explicit `.decode()` step — `Decimal(bytes)` raises `TypeError`. Do not rely on `bytes or "0"` defaults with `Decimal()` construction.

---

## Task 1 — Expand AgentCategory

Update `agents/base.py` to add the RISK category:

```python
class AgentCategory(str, Enum):
    TECHNICAL = "technical"
    DERIVATIVES = "derivatives"
    ONCHAIN = "onchain"
    SENTIMENT = "sentiment"
    RISK = "risk"  # Safety Layer — veto-only, not scored
```

## Task 2 — Risk Agent Implementation

Create `agents/risk/risk_agent.py`:

```python
class RiskAgent(BaseAgent):
    """Risk & Portfolio Management agent — ABSOLUTE VETO AUTHORITY.

    This agent is a PURE GATE, not a scoring participant.
    It contributes ZERO points to the confluence score (max_points=0).
    Its sole output is `veto: bool`. When True, the scorer forces
    decision=No Position regardless of the confluence score.

    Evaluates six risk dimensions (including HYDRA cascade awareness).
    If ANY dimension triggers a veto, the agent sets veto=True.

    Reads portfolio state from context (populated by orchestrator from Redis).
    """
    def __init__(self) -> None:
        super().__init__(
            name="risk_agent",
            category=AgentCategory.RISK,
            max_points=0,  # VETO-ONLY. Does NOT contribute to the 220-point score.
        )
```

The agent evaluates 6 risk dimensions. Each dimension's internal point allocation is for LOGGING/DIAGNOSTICS ONLY — none of these points flow into the confluence score.

**Dimension 1 — Drawdown Check (diagnostic, up to 20 pts)**
- Read `daily_drawdown_pct` and `weekly_drawdown_pct` from context (as `Decimal`)
- Daily drawdown > 3% → VETO (hard limit)
- Daily drawdown > 2% → score dimension at 50% (diagnostic only)
- Weekly drawdown > 7% → VETO
- No drawdown data → dimension score 0, no veto (fail-open on missing data, log CRITICAL)

**Dimension 2 — Exposure Check (diagnostic, up to 15 pts)**
- Read `total_exposure_pct` from context
- Exposure > 80% → VETO
- Exposure > 60% → dimension score at 50%, halve suggested position size
- Exposure < 30% → full dimension score

**Dimension 3 — Correlation Risk (diagnostic, up to 12 pts)**
- Read `open_positions` from context
- If proposed asset has >0.8 correlation with an existing open position, reduce effective position size by 50%
- If 3+ positions in same direction → concentration risk, reduce dimension score by 30%

**Dimension 4 — Volatility Gate (diagnostic, up to 12 pts)**
- Read `current_volatility_regime` from context (from HMM after Session 09, default "unknown")
- If regime is "high_volatility" → reduce max position size by 50%, reduce dimension score by 30%
- If recent 1h ATR > 2x 30-day average ATR → flag elevated volatility

**Dimension 5 — Circuit Breaker (diagnostic, up to 10 pts)**
- Read `consecutive_losses` from context
- 3 consecutive losses → reduce position size by 50%
- 5 consecutive losses → VETO (circuit breaker)
- Read `signals_last_hour` count — if > 10, reduce dimension score (overtrading guard)

**Dimension 6 — HYDRA Cascade Awareness (diagnostic, up to 8 pts)**
- Read the latest `HydraCascadeEvent` from context (populated by orchestrator from HYDRA buffer)
- If a Tier-3 liquidation cascade is actively occurring on the requested asset → reduce max position size by 50%, flag `is_cascade_triggered=True`
- If a Tier-4 cascade is occurring → **VETO** immediately
- If no cascade data available → dimension score 0, no veto (fail-open)

## Task 3 — Veto Field on AgentResult

Add to `AgentResult` in `agents/base.py`:

```python
class AgentResult(BaseModel, frozen=True):
    # ... existing fields ...
    veto: bool = Field(
        default=False,
        description="If True, forces No Position regardless of other agents"
    )
    veto_reasons: list[str] = Field(
        default_factory=list,
        description="Why the veto was triggered"
    )
```

Add validator: if `category != RISK` and `veto == True`, raise `ValueError`.
Only the Risk category is permitted to set `veto=True`.

## Task 4 — Wire Veto Into ConfluenceScorer

Update `pipeline/scorer.py`:
- Check for Risk veto FIRST — before any other scoring computation
- If vetoed: return signal with `decision=No Position`, `score=0`, `action=None`, `reasoning_summary="Risk veto: {reasons}"`
- The vetoed signal must still populate `key_risks` with the veto reasons
- If NOT vetoed: the Risk agent's result is excluded from the weighted sum (its `max_points=0` makes this a no-op but be explicit — filter `category=RISK` out of the scoring loop)

## Task 5 — Context Builder in Orchestrator

Update `PipelineOrchestrator.run()` to build risk context before dispatching agents.

**CRITICAL — Redis bytes decoding.** `redis.asyncio.Redis.get()` returns `bytes | None` by default. The previous version of this code had:

```python
# BROKEN — Decimal(bytes) raises TypeError
"daily_drawdown_pct": Decimal(await redis.get("portfolio:daily_drawdown") or "0"),
```

Python's `Decimal` constructor does not accept `bytes`. The `or "0"` fallback only helps when the key is missing (Redis returns `None`); when the key exists, Redis returns `bytes` and `Decimal(b"0.05")` raises `TypeError: conversion from bytes to Decimal is not supported`.

The correct pattern decodes the bytes to `str` first:

```python
async def _build_risk_context(self) -> dict:
    """Read portfolio state from Redis + HYDRA for the Risk agent."""
    redis = self._redis  # redis.asyncio client
    hydra = self._hydra_listener  # HydraStreamListener instance

    return {
        "daily_drawdown_pct": await self._read_decimal("portfolio:daily_drawdown"),
        "weekly_drawdown_pct": await self._read_decimal("portfolio:weekly_drawdown"),
        "total_exposure_pct": await self._read_decimal("portfolio:exposure"),
        "open_positions": await self._read_json_list("portfolio:positions"),
        "consecutive_losses": await self._read_int("portfolio:consecutive_losses"),
        "signals_last_hour": await self._read_int("portfolio:signals_1h_count"),
        "current_volatility_regime": await self._read_str("market:regime", default="unknown"),
        "hydra_cascade_event": hydra.get_latest_event(),  # Local buffer, no I/O
    }

async def _read_decimal(self, key: str, default: str = "0") -> Decimal:
    """Read a Decimal value from Redis, handling bytes → str correctly."""
    raw: bytes | None = await self._redis.get(key)
    if raw is None:
        return Decimal(default)
    try:
        return Decimal(raw.decode("utf-8"))
    except (InvalidOperation, UnicodeDecodeError) as exc:
        logger.warning("risk_context_decimal_parse_failed", key=key, err=str(exc))
        return Decimal(default)

async def _read_int(self, key: str, default: int = 0) -> int:
    raw: bytes | None = await self._redis.get(key)
    if raw is None:
        return default
    try:
        return int(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return default

async def _read_str(self, key: str, default: str = "") -> str:
    raw: bytes | None = await self._redis.get(key)
    return raw.decode("utf-8") if raw else default

async def _read_json_list(self, key: str) -> list:
    raw: bytes | None = await self._redis.get(key)
    if raw is None:
        return []
    try:
        return msgspec.json.decode(raw)
    except msgspec.DecodeError:
        return []
```

If Redis is unavailable, the wrapper methods return defaults (fail-open on missing data). The Risk agent's dimensions handle this: each dimension that sees zero/default context data scores itself at 0 but does NOT veto. Risk agent fails OPEN on missing data, CLOSED on threshold breach.

## Task 6 — Register in Registry

Add to `agents/registry.json`:
```json
{
    "name": "risk_agent",
    "category": "risk",
    "max_points": 0,
    "module_path": "agents.risk.risk_agent",
    "class_name": "RiskAgent",
    "enabled": true,
    "role": "veto_only"
}
```

## Quality Gates
1. `pytest agents/risk/test_risk_agent.py -v` — all pass
   - Test: daily drawdown 4% → veto=True
   - Test: daily drawdown 1% → no veto, dimensions pass
   - Test: exposure 85% → veto=True
   - Test: 5 consecutive losses → veto=True (circuit breaker)
   - Test: Tier-4 HYDRA cascade → veto=True
   - Test: Tier-3 HYDRA cascade → no veto, position size flagged for reduction
   - Test: all dimensions healthy → no veto
   - Test: missing context data → no veto, CRITICAL logged
   - Test: `max_points == 0` (VETO-ONLY assertion)
   - Test: `AgentResult.score` from Risk agent is always 0
   - Test: Redis returns bytes `b"0.05"` → context builder correctly decodes to `Decimal("0.05")` (regression test for the prior bytes bug)
   - Test: Redis returns `None` → context builder uses default `Decimal("0")`
   - Test: Redis returns malformed bytes `b"not-a-number"` → logs warning, returns default, does NOT raise
2. `pytest pipeline/test_scorer.py -v` — all pass
   - Test: Risk veto overrides 95-score signal → No Position
   - Test: Risk agent's zero score is excluded from weighted sum (filter works)
   - Test: No veto → normal scoring proceeds with 10 categories
3. `pytest pipeline/test_orchestrator.py -v` — risk context built correctly
4. `pyright --pythonversion 3.12 agents/risk/ pipeline/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import aioredis` — must be `import redis.asyncio`
- [ ] No `import json` — must be `import msgspec`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `float` for financial calculations — use `Decimal`
- [ ] No external HTTP calls for HYDRA data — read from local buffer
- [ ] No `print()` — use Loguru
- [ ] Veto validator: only RISK category can set `veto=True`
- [ ] `max_points=0` on the RiskAgent constructor (NOT 77, NOT any nonzero value)
- [ ] No "35% weight" or similar language anywhere in the agent's docstrings or comments
- [ ] `Decimal(raw.decode(...))` pattern — never `Decimal(await redis.get(...))`
- [ ] All functions ≤ 40 lines (each dimension is a private method)
