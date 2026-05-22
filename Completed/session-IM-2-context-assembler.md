# SESSION IM-2 — Intelligence Matrix: Context Assembler

## Context Files
@atlas/models/signal.py @atlas/orchestrator/ @atlas/shared/config.py

## Prerequisites
Session IM-1 (Pydantic Schemas) complete. `AgentResult`, `SubSignalResult`, and
all enum models must be importable from `atlas.models.signal` and
`atlas.models.enums`.

## Goal
Build the `ContextAssembler` — it converts agent outputs and multi-timeframe
concordance data into a structured prompt block for DeepSeek (the CARL framework
"Context" layer). The assembler is a pure function: no I/O, no state, no network.

**Architectural boundary:** The assembler is read-only data transformation. It
has no knowledge of the HTTP layer, no knowledge of exchanges, and no side effects.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` → `msgspec`.
   `pandas` → banned. `orjson` → banned. `pickle`/`joblib` → banned.
   `Pydantic.model_dump_json()` → banned.
3. **`PolarisSettings` only.** Never `os.getenv()`.
4. **40-line function limit.**
5. **Loguru canonical patterns only.** Positional `{}` format or `bind()`.
   Never un-referenced trailing kwargs (they are silently discarded). No f-strings.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **No stdlib `json`.** The assembler MUST use `msgspec.json.encode(...).decode("utf-8")`
   for any serialization step. `import json` is an automatic audit failure.
9. **Pure function.** No network, no Redis, no PostgreSQL, no file I/O. Any call
   that reaches outside the function arguments is a design violation.
10. **Required timeframes are fixed:** `["15m", "30m", "1h", "4h", "1d", "1w"]` —
    missing TFs render as `"Unknown"`.
11. **Tests live alongside code.** Create `atlas/orchestrator/test_context_assembler.py`,
    NOT `tests/test_context_assembler.py`.

---

## Task 1 — Context Assembler Implementation

Create `atlas/orchestrator/context_assembler.py`:

```python
"""CARL framework 'Context' layer — pure transformation, no I/O."""

from typing import Final

import msgspec
from loguru import logger

from atlas.models.signal import AgentResult


REQUIRED_TIMEFRAMES: Final[tuple[str, ...]] = ("15m", "30m", "1h", "4h", "1d", "1w")


class ContextAssembler:
    """Pure-function assembler — agent outputs → DeepSeek context block."""

    @staticmethod
    def build_matrix_string(
        asset: str,
        current_score: int,
        agent_results: list[AgentResult],
        mtf_context: dict[str, str],
    ) -> str:
        """Build the CARL 'Context' layer.

        Args:
            asset: Target asset symbol (e.g., 'BTCUSDT').
            current_score: Raw confluence score (0-220).
            agent_results: Per-agent verdicts with sub-signal matrices.
            mtf_context: Timeframe → state map (e.g., {"15m": "bullish"}).

        Returns:
            Prompt block ready for injection into the DeepSeek system prompt.
        """
        mtf_display = {tf: mtf_context.get(tf, "Unknown") for tf in REQUIRED_TIMEFRAMES}
        matrix = ContextAssembler._build_sub_signal_matrix(agent_results)
        matrix_json = msgspec.json.encode(matrix).decode("utf-8")
        mtf_block = ContextAssembler._build_mtf_block(mtf_display)

        logger.debug(
            "context matrix built | asset={} | score={} | agent_count={}",
            asset, current_score, len(agent_results),
        )

        return (
            f"ASSET: {asset}\n"
            f"CURRENT CONFLUENCE SCORE: {current_score}/220\n\n"
            f"MULTI-TIMEFRAME CONCORDANCE:\n{mtf_block}\n"
            f"AGENT SUB-SIGNAL MATRIX:\n{matrix_json}"
        )

    @staticmethod
    def _build_sub_signal_matrix(
        agent_results: list[AgentResult],
    ) -> dict[str, dict[str, object]]:
        """Collapse agent results into a JSON-serialisable matrix."""
        out: dict[str, dict[str, object]] = {}
        for ar in agent_results:
            out[ar.agent_name] = {
                "score_contribution": ar.score,
                "weight": ar.weight,
                "signals": {k: v.model_dump() for k, v in ar.sub_signals.items()},
            }
        return out

    @staticmethod
    def _build_mtf_block(mtf_display: dict[str, str]) -> str:
        """Render timeframe concordance as a bulleted list block."""
        return "".join(f"- {tf}: {state}\n" for tf, state in mtf_display.items())
```

**Note on `msgspec` vs `json`:**
`msgspec.json.encode()` returns `bytes`, not `str`. The `.decode("utf-8")` step is
mandatory. `msgspec` produces compact JSON — no `indent=2` equivalent. Pretty-print
is unnecessary here because the string feeds the LLM, not a human.

**Note on Loguru formatting:**
The `logger.debug` call above uses positional `{}` substitution — the three
values always appear in the output regardless of sink configuration. Writing
it as `logger.debug("context matrix built", asset=asset, score=current_score,
agent_count=len(agent_results))` would silently discard all three kwargs — they
are not referenced in the message template and are not captured into
`record["extra"]` because no `bind()` context was established.

## Task 2 — Tests

Create `atlas/orchestrator/test_context_assembler.py` (alongside the code — NOT in
a top-level `tests/` directory):

- Test: All 6 required timeframes present in output, even when `mtf_context` is empty
  (missing ones render as `"Unknown"`).
- Test: Output contains `"CURRENT CONFLUENCE SCORE: 142/220"` for `current_score=142`.
- Test: JSON matrix round-trips via `msgspec.json.decode` → dict with expected keys
  (`score_contribution`, `weight`, `signals`).
- Test: Empty `agent_results` list produces valid output with empty matrix `{}`.
- Test: `build_matrix_string` function body is ≤ 40 lines (AST-based line count).
- Test (grep-based): `grep -rn "import json" atlas/orchestrator/context_assembler.py`
  returns zero.
- Test (grep-based): no un-referenced trailing kwargs in logger calls.

## Quality Gates
1. `pytest atlas/orchestrator/test_context_assembler.py -v` — all pass.
2. `pyright --pythonversion 3.12 atlas/orchestrator/context_assembler.py` — zero errors.
3. `grep -rn "import json\|from json" atlas/orchestrator/context_assembler.py` — zero results.
4. `grep -rn "json.dumps\|json.loads" atlas/orchestrator/context_assembler.py` — zero results.
5. `grep -rn "logger.*f[\"']" atlas/orchestrator/context_assembler.py` — zero results.
6. `grep -rEn 'logger\.(info|error|warning|debug|critical)\([^,"]*,\s*\w+=' atlas/orchestrator/context_assembler.py` — zero results (catches un-referenced trailing kwargs).
7. `ls tests/test_context_assembler.py 2>&1 | grep "No such"` — absent (tests alongside code).

## Anti-Pattern Checklist
- [ ] No `import json` — `msgspec.json.encode(...).decode("utf-8")`
- [ ] No `json.dumps` / `json.loads` anywhere
- [ ] No f-strings in logger calls — positional `{}` format only
- [ ] No un-referenced trailing kwargs in logger calls
- [ ] No `os.getenv()` — PolarisSettings
- [ ] No I/O — pure transformation
- [ ] No `import aioredis` — N/A here (no Redis used)
- [ ] `REQUIRED_TIMEFRAMES` is `Final[tuple[...]]`, not a list
- [ ] All methods ≤ 40 lines
- [ ] Return type annotation present on every method
- [ ] Tests at `atlas/orchestrator/test_context_assembler.py`, never `tests/test_context_assembler.py`
