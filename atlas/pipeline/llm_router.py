"""LLM Router — routes assembled context to DeepSeek V3 or R1.

S3-P8 canonical implementation. DECIDES which DeepSeek model to use per request
based on three escalation criteria (confluence, anomalies, consistency).
All calls are wrapped in Langfuse traces for canonical observability.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

import httpx
import msgspec
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, SecretStr

if TYPE_CHECKING:
    from redis.asyncio import Redis
    from atlas.pipeline.context_assembler import AssembledContext
    from atlas.shared.config import PolarisSettings
    from atlas.models.signal import AgentResult, SignalOutput
    from atlas.telemetry.langfuse_client import LangfuseTelemetry


class LLMCallError(Exception):
    """Raised when an LLM call fails after retries."""

    def __init__(self, reason: str, *args: Any) -> None:
        self.reason = reason
        super().__init__(*args)


class LLMResponse(BaseModel):
    """Immutable response from the LLM Router."""

    model_config = ConfigDict(frozen=True)

    raw_text: str
    model_used: str
    escalation_reason: str | None
    input_tokens_est: int
    output_tokens_est: int
    latency_ms: float  # dimensionless duration — float OK
    route_decision: Literal["LOCAL", "API"]
    cycle_timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class LLMRouter:
    """Routes context to DeepSeek V3 (Routine) or R1 (High-Stakes).

    DeepSeek is the SOLE permitted LLM provider.
    Usage of any other LLM provider is strictly prohibited.
    Decisions are based on confluence scores, anomaly flags, and consistency.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        http_client: httpx.AsyncClient,
        redis_client: Redis,  # type: ignore[type-arg]
        langfuse_client: LangfuseTelemetry | None = None,
    ) -> None:
        """Initialize the router with injected dependencies.

        Validates that the DeepSeek API key is present at startup.
        """
        api_key = settings.deepseek_api_key.get_secret_value()
        if not api_key:
            raise ValueError("deepseek_api_key is required but missing or empty.")

        self._settings = settings
        self._http = http_client
        self._redis = redis_client
        self._langfuse = langfuse_client
        self._cost_queue: asyncio.Queue[tuple[str, int, int]] = asyncio.Queue(
            maxsize=500,
        )
        self._cost_worker: asyncio.Task[None] | None = None

    # ── Public API ────────────────────────────────────────────────────

    async def route_and_call(
        self,
        context: AssembledContext,
        signal: SignalOutput,
        provider_snapshots: dict[str, dict],
        agent_results: list[AgentResult],
    ) -> LLMResponse:
        """Determine route, call the chosen model, and track costs.

        Returns an ``LLMResponse`` with either the model output or an
        error marker.
        """
        model, reason = self._determine_route(
            signal, provider_snapshots, agent_results,
        )
        route: Literal["LOCAL", "API"] = (
            "API" if model == self._settings.router_api_model else "LOCAL"
        )

        try:
            raw_text, latency = await self._call_with_retry(
                model, context.system_prompt, context.user_message,
            )
        except LLMCallError as exc:
            logger.critical(
                "llm_router_failure_after_retries | model={} | reason={} | asset={}",
                model,
                exc.reason,
                signal.asset,
            )
            return LLMResponse(
                raw_text="PIPELINE_ERROR",
                model_used=model,
                escalation_reason=reason,
                input_tokens_est=context.estimated_tokens,
                output_tokens_est=0,
                latency_ms=0.0,
                route_decision=route,
            )

        input_tokens = context.estimated_tokens
        output_tokens = self._estimate_output_tokens(raw_text)

        # Queue cost tracking — never blocks hot path
        self._enqueue_cost(model, input_tokens, output_tokens)

        return LLMResponse(
            raw_text=raw_text,
            model_used=model,
            escalation_reason=reason,
            input_tokens_est=input_tokens,
            output_tokens_est=output_tokens,
            latency_ms=latency,
            route_decision=route,
        )

    # ── Routing Logic ─────────────────────────────────────────────────

    def _determine_route(
        self,
        signal: SignalOutput,
        provider_snapshots: dict[str, dict],
        agent_results: list[AgentResult],
    ) -> tuple[str, str | None]:
        """Apply escalation criteria in order to choose the model.

        Criteria:
            1. High Confluence (raw >= 140)
            2. Anomaly Flag (feature_scores or graph paths)
            3. Consistency Warning (cross-source mismatch)
        """
        # 1. High Confluence
        if signal.raw_confluence_score >= 140:
            res = (self._settings.router_api_model, "HIGH_CONFLUENCE")
            self._log_decision(*res)
            return res

        # 2. Anomaly Flags
        if self._has_anomaly_flags(signal, agent_results):
            res = (self._settings.router_api_model, "ANOMALY_FLAG")
            self._log_decision(*res)
            return res

        # 3. Consistency Warning
        if "_consistency_warning" in provider_snapshots:
            res = (self._settings.router_api_model, "CONSISTENCY_WARNING")
            self._log_decision(*res)
            return res

        # Default: V3 (Routine)
        res = (self._settings.router_local_model, None)
        self._log_decision(*res)
        return res

    def _has_anomaly_flags(
        self,
        signal: SignalOutput,
        agent_results: list[AgentResult],
    ) -> bool:
        """Check for anomaly indicators in agent results or graph paths."""
        for agent in agent_results:
            # Check sub_signals for feature_scores key (canonical pattern)
            f_scores = agent.sub_signals.get("feature_scores", {})
            if any("anomaly" in str(k).lower() and v for k, v in f_scores.items()):
                return True
        
        # Check graph paths
        if any("anomaly" in path.lower() for path in signal.contributing_graph_paths):
            return True
            
        return False

    def _log_decision(self, model: str, reason: str | None) -> None:
        """Log the routing decision with structured kwargs."""
        logger.info(
            "llm_router_decision | model={} | reason={}",
            model,
            reason or "ROUTINE",
        )

    # ── Model Execution ───────────────────────────────────────────────

    async def _call_with_retry(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
        max_retries: int = 2,
    ) -> tuple[str, float]:
        """Wrap _call_model with exponential backoff on failure."""
        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return await self._call_model(model, system_prompt, user_message)
            except LLMCallError as exc:
                last_exc = exc
                if attempt < max_retries:
                    logger.warning(
                        "llm_router_retry | attempt={} | model={} | error={}",
                        attempt + 1,
                        model,
                        exc.reason,
                    )
                    await asyncio.sleep(1)
                continue

        raise last_exc or LLMCallError("UNKNOWN_FAILURE")

    async def _call_model(
        self,
        model: str,
        system_prompt: str,
        user_message: str,
    ) -> tuple[str, float]:
        """Perform the actual HTTP call to DeepSeek with observability."""
        start_time = asyncio.get_event_loop().time()
        api_key = self._settings.deepseek_api_key.get_secret_value()

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": self._settings.router_max_tokens,
            "temperature": self._settings.router_temperature,
        }

        # Observability trace
        trace = None
        if self._langfuse:
            trace = self._langfuse.trace(
                name="llm_router_call",
                input={"system": system_prompt[:500], "user": user_message[:500]},
                metadata={"model": model},
            )

        try:
            response = await asyncio.wait_for(
                self._http.post(
                    self._settings.router_api_endpoint,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    content=msgspec.json.encode(payload),
                ),
                timeout=30.0,
            )
            response.raise_for_status()
            data = msgspec.json.decode(response.content)
            raw_text = data["choices"][0]["message"]["content"]
            
            latency = (asyncio.get_event_loop().time() - start_time) * 1000
            
            if trace:
                trace.update(
                    output={"text": raw_text[:500], "latency_ms": latency},
                )
            
            return raw_text, latency

        except asyncio.TimeoutError:
            if trace:
                trace.update(error="TIMEOUT", level="ERROR")
            raise LLMCallError("TIMEOUT")
        except httpx.HTTPStatusError as exc:
            if trace:
                trace.update(error=str(exc), level="ERROR")
            raise LLMCallError(f"HTTP_{exc.response.status_code}")
        except Exception as exc:
            if trace:
                trace.update(error=str(exc), level="ERROR")
            raise LLMCallError(f"CONNECTION_OR_DECODE_ERROR: {str(exc)}")

    # ── Cost Tracking & Helpers ──────────────────────────────────────

    def _estimate_output_tokens(self, text: str) -> int:
        """Estimate tokens as Whitespace-delimited words * 1.3."""
        return int(len(text.split()) * 1.3)

    async def _track_costs(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Increment Redis cost hash for the current day."""
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"llm_costs:{model}:{date_str}"
        
        try:
            # We use HINCRBY which is integer-native in Redis.
            # msgspec is NOT needed here as we aren't serializing a struct.
            pipe = self._redis.pipeline()
            pipe.hincrby(key, "calls", 1)
            pipe.hincrby(key, "input_tokens_est", input_tokens)
            pipe.hincrby(key, "output_tokens_est", output_tokens)
            pipe.expire(key, 30 * 86400)  # 30 days
            await pipe.execute()
        except Exception as exc:
            logger.error("llm_router_cost_tracking_failed | error={}", str(exc))

    # ── Bounded Cost Worker ───────────────────────────────────────────

    def _enqueue_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Queue cost tracking — never blocks the hot path.

        Drops silently if the bounded queue is full.
        """
        if self._cost_worker is None:
            self._cost_worker = asyncio.create_task(self._cost_worker_loop())
        try:
            self._cost_queue.put_nowait((model, input_tokens, output_tokens))
        except asyncio.QueueFull:
            logger.warning("llm_router_cost_queue_full — dropping cost entry")

    async def _cost_worker_loop(self) -> None:
        """Drain cost queue and batch-flush to Redis."""
        while True:
            batch: list[tuple[str, int, int]] = []
            try:
                batch.append(await asyncio.wait_for(
                    self._cost_queue.get(), timeout=2.0,
                ))
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            # Drain up to 50 items without blocking
            while len(batch) < 50:
                try:
                    batch.append(self._cost_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            for model, inp, out in batch:
                try:
                    await self._track_costs(model, inp, out)
                except Exception as exc:
                    logger.error("cost_worker_flush_error | error={}", str(exc))
                finally:
                    self._cost_queue.task_done()

    async def close(self) -> None:
        """Drain pending cost entries and cancel the worker."""
        if self._cost_worker is not None:
            await self._cost_queue.join()
            self._cost_worker.cancel()
            try:
                await self._cost_worker
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                cancelling = getattr(current_task, "cancelling", lambda: 0)
                if current_task is not None and cancelling():
                    raise
            self._cost_worker = None
