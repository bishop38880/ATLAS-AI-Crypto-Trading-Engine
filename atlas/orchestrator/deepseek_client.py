"""DeepSeek orchestrator — CARL-framework structured-output client."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

import httpx
import msgspec
import pybreaker  # type: ignore
from loguru import logger

from atlas.llm.response_parser import extract_json_from_response
from atlas.models.signal import (
    DeepSeekDecision,
    build_safe_fallback_decision,
)
from atlas.shared.config import PolarisSettings


from atlas.prompts.synthesis_output_prompt import SYNTHESIS_OUTPUT_FORMAT_INSTRUCTIONS

_SYSTEM_PROMPT = (
    "You are a senior quantitative crypto analyst for POLARIS. "
    "Analyze the AGENT SUB-SIGNAL MATRIX to find cross-category correlations. "
    "If strong correlations exist (e.g. On-Chain aligns with Derivatives), "
    "grade it ELEVATED or EXTREME. If contradictions exist, grade it STANDARD "
    "and lower confidence. You MUST respond with a valid JSON object matching "
    "the requested schema. No markdown wrappers. "
    "DO NOT dictate risk percentages or position sizes.\n\n"
    + SYNTHESIS_OUTPUT_FORMAT_INSTRUCTIONS
)


class _DeepSeekBreakerListener(pybreaker.CircuitBreakerListener):  # type: ignore
    """Listener to log circuit breaker state transitions."""

    def state_change(self, cb: Any, old_state: Any, new_state: Any) -> None:
        """Handle state transitions and log appropriately."""
        if new_state.name == "open":
            logger.warning(
                "deepseek circuit OPEN | reason={} | fail_count={}",
                "consecutive_failures",
                cb.fail_max,
            )
        elif new_state.name == "half_open":
            logger.info("deepseek circuit HALF-OPEN")
        elif new_state.name == "closed":
            logger.info("deepseek circuit CLOSED")


# Top-level breaker to share state across instances or requests
deepseek_breaker = pybreaker.CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="deepseek_orchestrator",
)
deepseek_breaker.add_listeners(_DeepSeekBreakerListener())


async def _await_post_and_parse_via_breaker(
    breaker: pybreaker.CircuitBreaker,
    post_and_parse: Any,
    payload: dict[str, Any],
) -> DeepSeekDecision:
    """Run an async DeepSeek POST under pybreaker without Tornado ``call_async``.

    ``CircuitBreaker.call_async`` is built on ``tornado.gen`` and raises
    ``NameError: name 'gen' is not defined`` when Tornado is not installed.
    This path mirrors ``CircuitBreakerState.call`` for asyncio coroutines.
    """

    with breaker._lock:
        if breaker.current_state == pybreaker.STATE_OPEN:
            opened_at = breaker._state_storage.opened_at
            timeout = timedelta(seconds=breaker.reset_timeout)
            if opened_at and datetime.now(UTC) < opened_at + timeout:
                raise pybreaker.CircuitBreakerError(
                    "Timeout not elapsed yet, circuit breaker still open",
                )
            breaker.half_open()

        breaker.state.before_call(post_and_parse, payload)
        for listener in breaker.listeners:
            listener.before_call(breaker, post_and_parse, payload)

    try:
        result = await post_and_parse(payload)
    except BaseException as exc:
        with breaker._lock:
            breaker.state._handle_error(exc)
        raise
    else:
        with breaker._lock:
            breaker.state._handle_success()
        return result


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
        try:
            payload = self._build_payload(context_matrix)
            async with self._semaphore:
                return await _await_post_and_parse_via_breaker(
                    deepseek_breaker,
                    self._post_and_parse,
                    payload,
                )
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
            "https://api.deepseek.com/v1/chat/completions",
            content=body,
            headers=headers,
        )
        response.raise_for_status()

        envelope = msgspec.json.decode(response.content)
        content_str = envelope["choices"][0]["message"]["content"]
        json_str = extract_json_from_response(content_str)
        parsed = msgspec.json.decode(json_str.encode("utf-8"))
        return DeepSeekDecision(**parsed)

    async def close(self) -> None:
        await self._client.aclose()
