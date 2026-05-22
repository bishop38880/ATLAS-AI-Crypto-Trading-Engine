# SESSION IM-1 — Intelligence Matrix: Pydantic Schemas

## Context Files
@atlas/models/signal.py @atlas/models/enums.py @atlas/shared/config.py @POLARIS_Context_Document_v3.md

## Prerequisites
Session 0 (BaseProvider scaffold) complete. **Session 00 (Signal Schema v2)
complete** — `SignalOutput`, `ActionBlock`, `SignalDecision`, `SignalDirection`,
`CategoryScores`, and `TelemetryEvent` must exist in `atlas/models/signal.py`.
This session **extends** Session 00's `SignalOutput`; it does NOT redefine it.

## Goal
Add the supporting Pydantic schemas for the Intelligence Matrix pipeline on top of
Session 00's canonical `SignalOutput`. New models:

- `CrossCorrelationGrade` (STANDARD / ELEVATED / EXTREME)
- `SubSignalResult` (granular agent-output data point)
- Extended `AgentResult` with `sub_signals` dict
- `DeepSeekDecision` (structured LLM output)
- Optional calibration fields layered onto `SignalOutput` via extension

**Architectural boundary:** ATLAS dictates the SIGNAL and the CORRELATION GRADE.
It NEVER calculates risk percentage, position size, or `amount`. PROMETHEUS
consumes the correlation grade and applies the 1.0% / 1.25% / 1.5% risk limits.

**Schema authority:** Session 00 is authoritative for `SignalOutput`. This
session does NOT redefine `action`, `decision`, `score`, `signal_id`, `asset`,
`timeframe`, `timestamp`, or `expires_at`. If a conflict exists between this
session and Session 00, Session 00 wins — file a correction to this session.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`. Ignore any `mypy` references.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` → `msgspec`.
   `pandas` → banned. `requests` → `httpx`. `pickle`/`joblib` → banned.
   `SQLAlchemy`/`psycopg2` → `asyncpg`. `sentence-transformers`/`FAISS`/`BM25`/`pgvector` → banned.
   `Pydantic.model_dump_json()` → banned (bypasses msgspec); use
   `msgspec.json.encode(model.model_dump())`.
3. **`PolarisSettings` only.** Never use `os.getenv()`.
4. **40-line function limit.**
5. **Loguru canonical patterns only.** Positional `{}` format or `bind()` —
   never un-referenced trailing kwargs (they are silently discarded).
   No f-strings.
6. **ATLAS has ZERO exchange awareness.** No CCXT, no Bitget client, no order
   management, no position sizing (no `amount`, no `notional`, no `leverage`).
7. **Test floor is sacred.** Never merge code that reduces the pytest count.
8. **Pydantic v2, `frozen=True` on every model.** No mutable state.
9. **All enums are `str, Enum`** — JSON-round-trippable, no raw integer enums.
10. **Session 00 parity.** `SignalOutput` already includes `signal_id`,
    `timestamp`, `decision`, `asset`, `timeframe`, `action`, `expires_at`,
    `reasoning_summary`, `key_convergences`, `key_risks`, `is_cascade_triggered`,
    `hydra_event_id`, `score` (0-100 normalised), `confidence`, `category_scores`,
    `contributing_graph_paths`, `telemetry`. Do NOT redefine or rename any of
    these fields. This session adds only what Session 00 does not provide.
11. **File path:** `atlas/models/signal.py` — singular, matching Session 00.
    Do not create `atlas/models/signals.py` (plural).
12. **Tests live alongside code:** `atlas/models/test_enums.py`,
    `atlas/models/test_sub_signal.py`, `atlas/models/test_deepseek_decision.py`.
    Not in a top-level `tests/` directory.

---

## Task 1 — Enums

Create `atlas/models/enums.py`:

```python
from enum import Enum


class CrossCorrelationGrade(str, Enum):
    """Correlation tier — PROMETHEUS maps to risk limit."""
    STANDARD = "STANDARD"    # PROMETHEUS applies 1.00% risk limit
    ELEVATED = "ELEVATED"    # PROMETHEUS applies 1.25% risk limit
    EXTREME = "EXTREME"      # PROMETHEUS applies 1.50% risk limit
```

`SignalDecision` and `SignalDirection` already live in `atlas/models/signal.py`
from Session 00. Do not redefine them here. If you find yourself typing
`class DecisionEnum` or `class DirectionEnum`, stop — use the Session 00 enums.

## Task 2 — SubSignal and Extended AgentResult

Append to `atlas/models/signal.py` (same file as Session 00's models):

```python
from pydantic import BaseModel, Field, field_validator


class SubSignalResult(BaseModel, frozen=True):
    """Granular data point from an agent — feeds the LLM correlation matrix."""
    value: str = Field(description="Human-readable value (e.g., '+2.8 SD')")
    flag: str = Field(description="Uppercase categorization (e.g., 'EXTREME_SHORT_CROWDING')")
    metadata: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("flag")
    @classmethod
    def _flag_uppercase(cls, v: str) -> str:
        if not v.isupper():
            raise ValueError("flag must be uppercase")
        return v
```

Extend the existing `AgentResult` (from `atlas/agents/base.py`, Session 00) by
adding a `sub_signals` field and a `score_contribution` field in a follow-up
migration within `atlas/agents/base.py` — do NOT create a competing `AgentResult`
in `atlas/models/signal.py`:

```python
# Added in-place to atlas/agents/base.py
class AgentResult(BaseModel, frozen=True):
    agent_name: str
    score: int = Field(ge=0, le=220, description="Raw 220-point contribution")
    weight: float = Field(ge=0.0, le=1.0)
    direction: SignalDirection = Field(default=SignalDirection.NEUTRAL)
    sub_signals: dict[str, SubSignalResult] = Field(
        default_factory=dict,
        description="Matrix data consumed by DeepSeek orchestrator"
    )
```

Note the scoring scale: `AgentResult.score` is the raw 220-point contribution
from this agent. `SignalOutput.score` is the **normalised 0-100** score that
the ConfluenceScorer produces by summing raw contributions and dividing by 220.
Both fields exist and are deliberately different — do not conflate them.

## Task 3 — DeepSeek Decision Schema

Append to `atlas/models/signal.py`:

```python
class DeepSeekDecision(BaseModel, frozen=True):
    """Strict JSON schema DeepSeek MUST adhere to.

    Note on enum coercion: Pydantic v2 will coerce valid strings to enums
    during instantiation by default. If a wire payload arrives with
    cross_correlation_grade="STANDARD" (string), Pydantic will accept it
    and coerce to CrossCorrelationGrade.STANDARD. This is intentional —
    the wire format is strings; the in-memory domain model is enums.

    If you need to reject string coercion (e.g. to catch caller bugs in
    internal code paths that should pass enum members), set
    `model_config = ConfigDict(strict=True)` — not `frozen=True`.
    `frozen=True` only prevents post-instantiation mutation.
    """
    decision: SignalDecision
    confidence: float = Field(ge=0.0, le=1.0)
    cross_correlation_grade: CrossCorrelationGrade
    key_convergences: list[str] = Field(description="Cross-agent correlations identified")
    key_risks: list[str] = Field(description="Cross-agent contradictions / risks")
    reasoning: str = Field(description="2-3 sentence synthesis", max_length=1200)
    would_change_if: str = Field(description="Single most important reversal condition")
```

## Task 4 — Calibration Fields Extension

Session 00's `SignalOutput` does not yet carry calibration fields (Session 17
hook). Add them as an optional extension to the existing `SignalOutput` class
in `atlas/models/signal.py`:

```python
# Append these two fields to the existing SignalOutput definition
# from Session 00 — do NOT create a parallel class.

class SignalOutput(BaseModel, frozen=True):
    # ... all existing Session 00 fields ...

    # === CALIBRATION (Session 17 hook) ===
    calibrated_probability: float | None = Field(
        default=None,
        ge=0.0, le=1.0,
        description="Isotonic-calibrated probability of profitable outcome. "
                    "None until Session 17 lands."
    )
    calibration_ece: float | None = Field(
        default=None,
        ge=0.0,
        description="Expected calibration error from current isotonic model. "
                    "None until Session 17 lands."
    )

    # === INTELLIGENCE MATRIX ===
    deepseek_evaluation: DeepSeekDecision | None = Field(
        default=None,
        description="Populated for high-conviction signals (raw score >= 140). "
                    "None for routine signals where DeepSeek is not invoked."
    )
    agent_breakdown: dict[str, AgentResult] = Field(
        default_factory=dict,
        description="Per-agent scoring contributions with sub-signal matrices."
    )
    raw_confluence_score: int = Field(
        default=0,
        ge=0, le=220,
        description="Raw 220-point confluence score before normalisation. "
                    "The normalised 0-100 form is in `score` (Session 00)."
    )
```

## Task 5 — Fallback Constructor (used by DeepSeek client)

Append a module-level helper to `atlas/models/signal.py`:

```python
def build_safe_fallback_decision(reason: str) -> DeepSeekDecision:
    """Neutral decision when DeepSeek evaluation fails.

    Used by deepseek_client.py on API failure. Returns HOLD / STANDARD
    so PROMETHEUS applies minimum risk limit — never amplifies on failure.

    Passes the enum member (not a bare string) for hygiene and to make
    the invariant explicit at the call site.
    """
    return DeepSeekDecision(
        decision=SignalDecision.HOLD,
        confidence=0.0,
        cross_correlation_grade=CrossCorrelationGrade.STANDARD,
        key_convergences=["API_FAILURE_FALLBACK"],
        key_risks=["API_FAILURE_FALLBACK"],
        reasoning=f"Fallback decision: {reason}",
        would_change_if="Upstream evaluation path recovers.",
    )
```

## Task 6 — Tests

Create the following files **alongside the code they test** (never in top-level `tests/`):

**`atlas/models/test_sub_signal.py`:**
- Test: `SubSignalResult` rejects lowercase flag.
- Test: `SubSignalResult` accepts upper-snake-case flag like `EXTREME_SHORT_CROWDING`.

**`atlas/models/test_deepseek_decision.py`:**
- Test: `DeepSeekDecision.confidence = 1.5` raises.
- Test: `cross_correlation_grade="STANDARD"` (string) is coerced to the enum by
  default Pydantic behaviour — this is intentional for wire-format compatibility.
- Test: `build_safe_fallback_decision("timeout")` returns `HOLD`, `STANDARD`,
  `confidence = 0.0`.

**`atlas/models/test_signal_intelligence_extensions.py`:**
- Test: `AgentResult.score > 220` raises `ValidationError`.
- Test: `SignalOutput` round-trips via `msgspec.json.encode(model.model_dump())`
  and `msgspec.json.decode` → dict → `SignalOutput(**d)` unchanged.
  **Do NOT use `SignalOutput.model_dump_json()` — banned (bypasses msgspec).**
- Test: `SignalOutput.raw_confluence_score` default is 0 and accepts range 0–220.
- Test: `SignalOutput.calibrated_probability` default is None and accepts 0.0–1.0.
- Test: `SignalOutput.deepseek_evaluation` default is None; populated path
  validates.

## Quality Gates
1. `pytest atlas/models/ -v` — all pass. Confirms tests live alongside code.
2. `pyright --pythonversion 3.12 atlas/models/` — zero errors.
3. `grep -rn "os.getenv\|import json\|import requests\|aioredis\|model_dump_json" atlas/models/ --include="*.py"` — zero results.
4. `grep -rEn 'logger\.(info|error|warning|debug|critical)\([^,"]*,\s*\w+=' atlas/models/ --include="*.py"` — zero results (catches un-referenced-kwargs pattern; accepts positional and `bind()`).
5. `grep -rn "logger.*f[\"']" atlas/models/ --include="*.py"` — zero results.
6. `grep -rn "class DecisionEnum\|class DirectionEnum\|class ActionEnum" atlas/ --include="*.py"` — zero results (these enums belong to Session 00 as `SignalDecision` and `SignalDirection`; `ActionEnum` was deleted from IM-1).
7. `grep -rn "frozen=True" atlas/models/signal.py` — appears on every `BaseModel`.
8. `ls atlas/models/signals.py` — does NOT exist (singular filename is canonical).
9. `ls tests/test_signal_models.py tests/test_deepseek_client.py tests/test_context_assembler.py 2>&1 | grep "No such"` — all three absent (tests live next to code).

## Anti-Pattern Checklist
- [ ] `SignalOutput` NOT redefined — only extended with calibration, DeepSeek, raw_confluence_score, agent_breakdown fields
- [ ] No `DecisionEnum` / `DirectionEnum` / `ActionEnum` — use Session 00's `SignalDecision`, `SignalDirection`; `action` is `ActionBlock | None`, not an enum
- [ ] `CrossCorrelationGrade.STANDARD` (enum member) used in `build_safe_fallback_decision` for explicitness
- [ ] File is `atlas/models/signal.py` (singular), not `atlas/models/signals.py`
- [ ] No `model_dump_json()` anywhere — use `msgspec.json.encode(model.model_dump())`
- [ ] No `import json` — msgspec for encode/decode
- [ ] No `os.getenv()` — PolarisSettings
- [ ] No f-strings in logger calls; no un-referenced kwargs in logger calls
- [ ] Every model is `frozen=True`
- [ ] Every enum inherits `(str, Enum)` for JSON round-trip
- [ ] All functions ≤ 40 lines
- [ ] Tests live at `atlas/models/test_*.py`, never `tests/test_*.py`
- [ ] No claim that `frozen=True` enforces strict enum validation — that's `ConfigDict(strict=True)`
