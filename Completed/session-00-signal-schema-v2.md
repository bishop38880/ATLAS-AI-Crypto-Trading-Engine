# SESSION 00 — Signal Schema v2: Direction, Action Block, Signal TTL & HYDRA Integration

## Context Files
@atlas/models/signal.py @atlas/models/telemetry.py @atlas/agents/base.py @atlas/orchestrator/scorer.py @atlas/orchestrator/orchestrator.py @atlas/shared/config.py @atlas/core/state.py

## Prerequisites
None — this is the first session. Run BEFORE any other session.

## Goal
The current `SignalOutput` contains a score, confidence, and category breakdown — but
no trade direction, no asset identifier, no timeframe, no stop loss, no take profit,
and no expiration. PROMETHEUS cannot execute a signal that says "87" without knowing
"buy BTCUSDT on 30m with SL at 65800 and TP at 71000." This session expands the
signal schema to match the canonical format, adds TTL-based expiration so stale
signals are never acted upon, and wires in HYDRA cascade event linkage so signals
generated during liquidation events carry the event signature for frontend heatmap correlation.

**CRITICAL:** This session changes the core data model that every downstream component
depends on. Every test in the codebase that constructs a `SignalOutput` will need updating.

**HARD WALL:** ATLAS produces **price levels and direction only**. Position sizing
(`amount`, `notional`, `leverage`) is computed exclusively by PROMETHEUS's
RiskEnforcer from portfolio equity, correlation-grade-derived risk limit, and
entry-to-stop distance. `ActionBlock` in this session does **NOT** contain `amount`.
If a future session proposes adding `amount` to `ActionBlock`, reject it — that is
the Session-00 hard-wall breach.

---

## NON-NEGOTIABLE INVARIANTS (read before writing any code)
1. **Pyright only.** Ignore any legacy references to `mypy`. Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis`, `pandas`, `requests`, `orjson`, stdlib `json`, `FAISS`, `BM25`, `SQLAlchemy`, `psycopg2`, `pickle`, `joblib`, `sentence-transformers`, `Pydantic.model_dump_json()` are all **BANNED**. Use `redis.asyncio` for Redis, `msgspec` for JSON, `asyncpg` for PostgreSQL.
3. **`PolarisSettings` only.** Never use `os.getenv()`. All env vars read through `PolarisSettings`.
4. **40-line function limit.** Extract helpers for anything longer.
5. **Loguru canonical patterns only.** `logger.info("msg | key={}", val)` (positional) or `logger.bind(key=val).info("msg")` (extras). NEVER `logger.info("msg", key=val)` where the key is not in the format string — kwargs are discarded in that form. NEVER f-strings.
6. **ATLAS has ZERO exchange awareness.** No orders, positions, credentials, execution logic, or position-size computation. `ActionBlock` defines *suggested* price levels and direction for PROMETHEUS — ATLAS never executes, never sizes positions.
7. **Test floor is sacred.** `pytest` count must not decrease.
8. **`Decimal` for all financial fields.** `price`, `stop_loss`, `take_profit` in `ActionBlock` must use `Decimal`. Scores and percentages use `int` or `float`.
9. **Frozen Pydantic models.** All data models use `frozen=True`.
10. **Signal ID is the canonical correlation key.** Every signal MUST have a `signal_id: str` field. PROMETHEUS and the Post-Trade Learning MCP match outcomes to original signals by this ID. Do NOT rely on timestamp+asset concatenation — small format disagreements between systems will cause silent matching failures.
11. **Tests live alongside code.** `atlas/models/test_signal.py`, not `tests/test_signal.py`.

---

## Task 1 — Expand SignalOutput Schema

Update `atlas/models/signal.py`:

```python
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field, field_validator, model_validator

class SignalDecision(str, Enum):
    """The six permitted decision values — no other values allowed."""
    STRONG_BUY = "Strong Buy"
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"
    STRONG_SELL = "Strong Sell"
    NO_POSITION = "No Position"

class ActionBlock(BaseModel, frozen=True):
    """Trade parameters — only populated for Buy/Sell decisions.

    ATLAS suggests price levels and direction. PROMETHEUS decides whether
    to execute, and is the exclusive owner of position sizing. All price
    fields use Decimal for precision.

    NOTE: `amount` is deliberately absent. Position sizing is PROMETHEUS's
    responsibility — it requires portfolio equity, correlation-grade risk
    limit, and leverage, none of which ATLAS has visibility into.
    """
    side: str = Field(pattern="^(buy|sell)$")
    order_type: str = Field(default="limit", pattern="^(limit|market)$")
    price: Decimal = Field(gt=0, description="Suggested entry price")
    stop_loss: Decimal = Field(gt=0, description="Suggested stop loss level")
    take_profit: Decimal = Field(gt=0, description="Suggested take profit level")

class SignalOutput(BaseModel, frozen=True):
    # --- IDENTITY ---
    signal_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description=(
            "Canonical correlation key. PROMETHEUS records this on order placement "
            "and passes it back verbatim in TradeOutcome.signal_id. Never synthesise "
            "from timestamp+asset — that breaks cross-system matching."
        ),
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decision: SignalDecision = Field(description="One of six permitted values")
    asset: str = Field(min_length=1, description="e.g. BTCUSDT")
    timeframe: str = Field(default="30m", description="Primary decision timeframe")

    # --- ACTION ---
    action: ActionBlock | None = Field(
        default=None,
        description="Populated for Buy/Sell decisions only. None for Hold/No Position."
    )
    expires_at: datetime = Field(
        description="UTC timestamp after which PROMETHEUS must discard this signal"
    )

    # --- REASONING ---
    reasoning_summary: str = Field(default="", description="2-3 sentence synthesis")
    key_convergences: list[str] = Field(default_factory=list)
    key_risks: list[str] = Field(default_factory=list)

    # --- HYDRA CASCADE LINKAGE ---
    is_cascade_triggered: bool = Field(
        default=False,
        description="True if this signal was generated during an active HYDRA cascade"
    )
    hydra_event_id: str | None = Field(
        default=None,
        description="Links to the specific liquidation cascade matrix ID from HYDRA"
    )

    # --- SCORING ---
    score: int = Field(
        ge=0, le=100,
        description="Normalised confluence score (0-100). Raw 220-point score "
                    "lives in `raw_confluence_score` on the intelligence matrix "
                    "extension — Session IM-1."
    )
    confidence: float = Field(ge=0.0, le=1.0)
    category_scores: CategoryScores
    contributing_graph_paths: list[str] = Field(default_factory=list)
    telemetry: TelemetryEvent

    @field_validator("signal_id")
    @classmethod
    def _signal_id_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("signal_id must be non-empty")
        return v

    @model_validator(mode="after")
    def _action_matches_decision(self) -> "SignalOutput":
        actionable = {
            SignalDecision.BUY, SignalDecision.STRONG_BUY,
            SignalDecision.SELL, SignalDecision.STRONG_SELL,
        }
        if self.decision in actionable and self.action is None:
            raise ValueError(f"decision={self.decision.value} requires an ActionBlock")
        if self.decision not in actionable and self.action is not None:
            raise ValueError(f"decision={self.decision.value} must have action=None")
        if self.expires_at <= self.timestamp:
            raise ValueError("expires_at must be strictly after timestamp")
        if self.is_cascade_triggered and self.hydra_event_id is None:
            raise ValueError("is_cascade_triggered=True requires hydra_event_id")
        return self
```

## Task 2 — Signal TTL Configuration

Add to `atlas/shared/config.py` (via `PolarisSettings` — never `os.getenv()`):

```python
signal_ttl_minutes: dict[str, int] = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}
```

For high-frequency cascade-triggered signals, override with a shorter TTL:
```python
# 2 minutes gives PROMETHEUS enough headroom to queue, validate (RiskEnforcer),
# submit to Bitget, and receive fill confirmation without the signal going stale
# mid-pipeline. A 1-minute TTL was rejected as too aggressive — if PROMETHEUS is
# momentarily busy with another order or kill-switch evaluation, a 60s budget
# leaves no margin for processing jitter.
cascade_signal_ttl_minutes: int = 2
```

## Task 3 — Decision Mapper

Create `atlas/signals/decision_mapper.py`:

```python
def calculate_decision(
    score: int,
    category_scores: CategoryScores,
    risk_veto: bool = False,
) -> SignalDecision:
    """Map normalized score to decision.

    Rules:
    - risk_veto=True → No Position (always, regardless of score)
    - score >= 80 → Strong Buy or Strong Sell (direction from agent consensus)
    - score >= 60 → Buy or Sell
    - score >= 40 → Hold
    - score < 40  → No Position

    Direction (buy vs sell) is determined by the sign of the directional
    consensus across agents — see Task 4.
    """
```

## Task 4 — Add Direction to AgentResult

Expand `AgentResult` in `atlas/agents/base.py`:

```python
class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"

class AgentResult(BaseModel, frozen=True):
    # ... existing fields ...
    direction: SignalDirection = Field(
        default=SignalDirection.NEUTRAL,
        description="Agent's directional conviction"
    )
```

Update all 4 existing agents to set `direction` based on their scoring logic.
If an agent scores above 60% of its max points, direction is BULLISH. Below 40%, BEARISH.
Between 40-60%, NEUTRAL. (Session 09 HMM replaces this with regime-aware direction.)

## Task 5 — Update ConfluenceScorer

Update `atlas/orchestrator/scorer.py`:
- Accept `asset: str` and `timeframe: str` parameters
- Call `calculate_decision()` to set the decision field
- Generate a fresh `signal_id` via `uuid4()` at the top of scoring (the `default_factory` on the model handles this, but log the ID explicitly for traceability: `logger.info("scoring start | signal_id={} | asset={}", signal_id, asset)`)
- Compute `expires_at` from config TTL map (use `cascade_signal_ttl_minutes` if `is_cascade_triggered`)
- Build `ActionBlock` with `Decimal` fields for `price`, `stop_loss`, `take_profit` when decision is actionable. **Do NOT compute an `amount` field — it no longer exists on `ActionBlock`.**
- Aggregate `reasoning_summary` from agent explanations
- Collect `key_convergences` and `key_risks`

## Task 6 — Update State Management

Update `atlas/core/state.py`:
- Store expanded signal fields including `signal_id`
- Use the signal's own `asset` field instead of taking `symbol` as a separate parameter
- Serialize with `msgspec.json.encode(signal.model_dump())` (never stdlib `json`, never `model_dump_json()`)
- The `signal_id` field is indexed — PROMETHEUS lookups by `signal_id` must be O(1) in the hot path

## Quality Gates
1. `pytest atlas/models/test_signal.py -v` — all pass
   - Test: Buy decision requires non-None action block
   - Test: Hold decision requires None action block
   - Test: expires_at must be after timestamp
   - Test: score clamping still works (0-100)
   - Test: invalid decision string rejected
   - Test: cascade_triggered=True requires hydra_event_id
   - Test: ActionBlock price fields are `Decimal`
   - Test: `ActionBlock` does NOT have an `amount` field (`hasattr(ActionBlock.model_fields, 'amount')` is False)
   - Test: `signal_id` is auto-generated as valid UUID4 if not provided
   - Test: `signal_id` round-trips through `msgspec.json.encode(signal.model_dump())` / `decode` unchanged
   - Test: empty string `signal_id` raises ValidationError
2. `pytest atlas/signals/test_decision_mapper.py -v` — all pass
   - Test: score 85 + bullish consensus → Strong Buy
   - Test: score 85 + bearish consensus → Strong Sell
   - Test: risk_veto=True + score 95 → No Position
   - Test: score 35 → No Position regardless of direction
3. `pytest atlas/orchestrator/test_scorer.py -v` — all pass (updated for new schema)
4. `pyright --pythonversion 3.12 atlas/models/ atlas/signals/ atlas/orchestrator/` — zero errors

## Anti-Pattern Checklist (verify before committing)
- [ ] No `import json` — must be `import msgspec`
- [ ] No `model_dump_json()` — must be `msgspec.json.encode(model.model_dump())`
- [ ] No `os.getenv()` — must use `PolarisSettings`
- [ ] No `float` for price/SL/TP — must use `Decimal`
- [ ] No `amount` field on `ActionBlock` — PROMETHEUS's concern
- [ ] No `mypy` — use `pyright`
- [ ] No `print()` — use Loguru with canonical patterns
- [ ] No un-referenced kwargs in logger calls — use positional `{}` or `bind()`
- [ ] All models use `frozen=True`
- [ ] All functions ≤ 40 lines
- [ ] `signal_id` field present on `SignalOutput` with default_factory=uuid4
- [ ] `signal_id` is NOT synthesised from timestamp+asset anywhere
- [ ] `cascade_signal_ttl_minutes` is 2 (not 1)
- [ ] Tests live at `atlas/models/test_signal.py`, not `tests/test_signal.py`
