# SESSION IM-3 — Intelligence Matrix: DeepSeek Orchestrator Client

## Context Files
@atlas/models/signal.py @atlas/orchestrator/context_assembler.py @atlas/shared/config.py @atlas/core/circuit_breaker.py

## Prerequisites
- Session IM-1 (Pydantic Schemas) complete — `DeepSeekDecision`, `SignalDecision`,
  `CrossCorrelationGrade`, and `build_safe_fallback_decision` must be importable
  from `atlas.models.signal`.
- Session IM-2 (Context Assembler) complete — produces the prompt payload.
- Session 02 (Circuit Breakers) complete — `pybreaker` infrastructure available.

## Goal
Build the async DeepSeek orchestrator client. It sends the CARL-framework context
matrix to DeepSeek, receives structured JSON, parses into a `DeepSeekDecision`, and
returns. On any failure it returns a safe HOLD/STANDARD fallback — never raises.

**Architectural boundary:** This is the sole cloud-LLM path in ATLAS. No other
module in ATLAS may call DeepSeek directly. PROMETHEUS consumes the resulting
decision via the signal bus — not by calling this client.

---

## NON-NEGOTIABLE INVARIANTS

1. **Pyright only.** Run `pyright --pythonversion 3.12`.
2. **No banned libraries.** `aioredis` → `redis.asyncio`. stdlib `json` → `msgspec`.
   `requests` → `httpx`. `orjson` → banned. `pickle`/`joblib` → banned.
   `Pydantic.model_dump_json()` → banned.
3. **`PolarisSettings` only.** `os.getenv()` is an automatic audit failure.
4. **40-line function limit.** `evaluate_signal` MUST be decomposed —
   `_build_payload`, `_post_and_parse`, `_safe_fallback` are separate helpers.
5. **Loguru canonical patterns only.** Positional `{}` format (`logger.error(
   "failed | error={}", str(e))`) or `bind()`. Never un-referenced trailing
   kwargs — they are silently discarded. No f-strings.
6. **ATLAS has ZERO exchange awareness.**
7. **Test floor is sacred.**
8. **URL literal hygiene.** The DeepSeek endpoint URL is a plain string literal.
   Any markdown, brackets, parentheses, or escape sequences in the URL literal
   are a fatal bug. Canonical URL:
   `https://api.deepseek.com/v1/chat/completions` — nothing else.
9. **Never raise.** This client is load-bearing in the scoring pipeline. On ANY
   failure (HTTP, parse, validation, timeout), return
   `build_safe_fallback_decision(reason)` — HOLD / STANDARD / confidence 0.0.
10. **Enum discipline.** `cross_correlation_grade` is a `CrossCorrelationGrade` enum
    member in the fallback helper (`build_safe_fallback_decision`, IM-1). At the
    parse boundary, Pydantic v2 will coerce a valid string to the enum by
    default — this is intentional for wire compatibility. Do not add
    `ConfigDict(strict=True)` here; the wire format is strings.
11. **msgspec for decode — NEVER `response.json()`.** `response.json()` uses
    httpx's stdlib-json internals (banned path). Decode via
    `msgspec.json.decode(response.content)` to a dict, then construct the
    Pydantic model.
12. **Tests live alongside code.** `atlas/orchestrator/test_deepseek_client.py`,
    not `tests/test_deepseek_client.py`.

---

## Task 1 — Settings

Add to `PolarisSettings` (in `atlas/shared/config.py`):

```python
class PolarisSettings(BaseSettings):
    # ... existing fields ...

    # DeepSeek
    deepseek_api_key: SecretStr
    deepseek_base_url: str = "https://api.deepseek.com/v1/chat/completions"
    deepseek_model: str = "deepseek-chat"
    deepseek_timeout_seconds: float = 30.0
    deepseek_connect_timeout_seconds: float = 5.0
    deepseek_max_concurrent: int = 10
    deepseek_temperature: float = 0.1
```

## Task 2 — Client Implementation

Create `atlas/orchestrator/deepseek_client.py`:

```python
"""DeepSeek orchestrator — CARL-framework structured-output client."""

import asyncio
from typing import Any

import httpx
import msgspec
from loguru import logger

from atlas.models.signal import (
    DeepSeekDecision,
    build_safe_fallback_decision,
)
from atlas.shared.config import PolarisSettings


_SYSTEM_PROMPT = (
    "You are a senior quantitative crypto analyst for POLARIS. "
    "Analyze the AGENT SUB-SIGNAL MATRIX to find cross-category correlations. "
    "If strong correlations exist (e.g. On-Chain aligns with Derivatives), "
    "grade it ELEVATED or EXTREME. If contradictions exist, grade it STANDARD "
    "and lower confidence. You MUST respond with a valid JSON object matching "
    "the requested schema. No markdown wrappers. "
    "DO NOT dictate risk percentages or position sizes."
)


class DeepSeekOrchestratorClient:
    """Async orchestrator client — never raises, always returns a decision."""

    def __init__(self, settings: PolarisSettings) -> None:
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.deepseek_max_concurrent)
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.deepseek_timeout_seconds,
                connect=settings.deepseek_connect_timeout_seconds,
            ),
            http2=True,
        )

    async def evaluate_signal(self, context_matrix: str) -> DeepSeekDecision:
        """Top-level evaluation entry point — always returns a decision."""
        payload = self._build_payload(context_matrix)
        try:
            async with self._semaphore:
                return await self._post_and_parse(payload)
        except Exception as e:
            logger.error(
                "DeepSeek evaluation failed | error={} | exc_type={}",
                str(e), type(e).__name__,
            )
            return build_safe_fallback_decision(f"{type(e).__name__}: {e}")

    def _build_payload(self, context_matrix: str) -> dict[str, Any]:
        """Construct the DeepSeek chat completion payload."""
        return {
            "model": self._settings.deepseek_model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": context_matrix},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self._settings.deepseek_temperature,
        }

    async def _post_and_parse(self, payload: dict[str, Any]) -> DeepSeekDecision:
        """POST to DeepSeek, parse structured JSON, validate via Pydantic."""
        headers = {
            "Authorization": f"Bearer {self._settings.deepseek_api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        body = msgspec.json.encode(payload)

        response = await self._client.post(
            self._settings.deepseek_base_url,
            content=body,
            headers=headers,
        )
        response.raise_for_status()

        envelope = msgspec.json.decode(response.content)
        content_str = envelope["choices"][0]["message"]["content"]
        parsed = msgspec.json.decode(content_str.encode("utf-8"))
        return DeepSeekDecision(**parsed)

    async def close(self) -> None:
        await self._client.aclose()
```

**Decomposition rationale:**
- `evaluate_signal` is 13 lines — well under the 40-line limit.
- `_build_payload` is pure and synchronous — testable in isolation.
- `_post_and_parse` contains all I/O + parse + validate — single responsibility.
- Exception boundary is at the top level only — no partial recovery, no nested try/except.

**Decode pattern note:** `msgspec.json.decode(content_str.encode("utf-8"))` returns
a plain `dict`. We then construct the Pydantic `DeepSeekDecision` via
`DeepSeekDecision(**parsed)`. This is the correct pattern for any Pydantic
`BaseModel` — `msgspec.json.decode(type=PydanticModel)` does NOT work because
msgspec's `type=` parameter supports `msgspec.Struct`, dataclasses, `TypedDict`,
`NamedTuple`, and standard scalars, but NOT Pydantic `BaseModel`.

## Task 3 — Circuit Breaker Integration

Wrap `_post_and_parse` with a `pybreaker.CircuitBreaker` from Session 02. Create
a named breaker `deepseek_orchestrator` with:
- `fail_max=5` (5 consecutive failures trip the breaker)
- `reset_timeout=60` (wait 60s before half-open)
- When OPEN: skip HTTP call entirely, return fallback immediately.

Logging on state transition (positional format):
```python
logger.warning("deepseek circuit OPEN | reason={} | fail_count={}", reason, count)
logger.info("deepseek circuit HALF-OPEN")
logger.info("deepseek circuit CLOSED")
```

## Task 4 — Tests

Create `atlas/orchestrator/test_deepseek_client.py` (alongside the code — NOT in
a top-level `tests/` directory):

- Test: Valid mock response → `DeepSeekDecision` with correct enum values.
- Test: HTTP 500 → fallback decision (HOLD, STANDARD, confidence 0.0).
- Test: Timeout → fallback decision.
- Test: Malformed JSON in response → fallback decision.
- Test: Response missing required field (`decision`) → fallback.
- Test: `cross_correlation_grade` arrives as string `"STANDARD"` → parsed to enum
  (Pydantic default coercion — intentional for wire compatibility).
- Test: Client never raises — every path returns a `DeepSeekDecision`.
- Test: URL is exactly `"https://api.deepseek.com/v1/chat/completions"` — no
  markdown, no brackets, no escape sequences. (grep-based assertion.)
- Test: `logger.error` called with positional `{}` format, not un-referenced
  kwargs and not f-strings. (grep-based assertion.)
- Test: Circuit breaker OPEN → no HTTP call, fallback returned.
- Test: Semaphore limits concurrent requests to `max_concurrent`.

## Quality Gates
1. `pytest atlas/orchestrator/test_deepseek_client.py -v` — all pass.
2. `pyright --pythonversion 3.12 atlas/orchestrator/deepseek_client.py` — zero errors.
3. `grep -rn "os.getenv\|os.environ" atlas/orchestrator/deepseek_client.py` — zero.
4. `grep -rn "import json\|import requests\|aioredis\|response\.json()" atlas/orchestrator/deepseek_client.py` — zero.
5. `grep -rn "logger.*f[\"']" atlas/orchestrator/deepseek_client.py` — zero.
6. `grep -rEn 'logger\.(info|error|warning|debug|critical)\([^,"]*,\s*\w+=' atlas/orchestrator/deepseek_client.py` — zero (catches un-referenced trailing kwargs).
7. `grep -rn "\[https://\|\](https://" atlas/orchestrator/deepseek_client.py` — zero
   (catches accidental markdown link syntax in URL literals).
8. `grep -rn "cross_correlation_grade=[\"']" atlas/orchestrator/deepseek_client.py` — zero
   (fallback uses enum member, not bare string).
9. `ls tests/test_deepseek_client.py 2>&1 | grep "No such"` — absent (tests alongside code).

## Anti-Pattern Checklist
- [ ] URL literal is clean: `"https://api.deepseek.com/v1/chat/completions"` — no markdown
- [ ] `PolarisSettings.deepseek_api_key.get_secret_value()` — NEVER `os.getenv`
- [ ] `logger.error("msg | key={}", val)` — NEVER `logger.error("msg", key=val)` or `logger.error(f"msg: {val}")`
- [ ] `msgspec.json.decode(response.content)` → dict → Pydantic — NEVER `response.json()`, NEVER `msgspec.json.decode(..., type=PydanticModel)`
- [ ] `msgspec.json.encode(payload)` — NEVER `json.dumps(payload)`, NEVER `model_dump_json()`
- [ ] Fallback uses `build_safe_fallback_decision()` helper from Session IM-1
- [ ] Every function ≤ 40 lines
- [ ] Client never raises — every exception path returns fallback
- [ ] Circuit breaker wired for DeepSeek endpoint
- [ ] Semaphore limits concurrency to `settings.deepseek_max_concurrent`
- [ ] Async client closed via explicit `close()` method (no context-manager assumption)
- [ ] Tests at `atlas/orchestrator/test_deepseek_client.py`, never `tests/test_deepseek_client.py`
