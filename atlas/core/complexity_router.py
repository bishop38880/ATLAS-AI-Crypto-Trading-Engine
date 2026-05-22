"""Complexity Router Layer.

Routes tasks to appropriate models based on rules, checking cache first.
"""

from typing import Any

from loguru import logger

from atlas.core.llm_client import BaseLLMClient, LLMResponse
from atlas.rag.cache_manager import CacheManager
from atlas.shared.config import ModelStackConfig

ROUTINE_TASKS = {
    "sentiment_classification",
    "news_headline_scoring",
    "provider_summary_formatting",
    "regime_classification_ohlcv",
    "moderate_conviction_reasoning",
}

HIGH_STAKES_TASKS = {
    "conflicting_signal_synthesis",
    "adversarial_trade_review",
    "novel_market_event_analysis",
    "high_conviction_reasoning",
}


def _build_cached_response(cached: Any, task_name: str) -> LLMResponse:
    """Build an LLMResponse from a cache hit."""
    logger.info("Cache hit for task | task={} | tier={}", task_name, cached.tier)
    return LLMResponse(
        text=cached.entry.response,
        reasoning=None,
        model="cache",
        latency_ms=0,
        tokens_used=0,
        cost_estimate_usd=0.0,
    )


class ComplexityRouter:
    """Routes prompts to the appropriate LLM based on task complexity."""

    def __init__(
        self,
        config: ModelStackConfig,
        cache_manager: CacheManager,
        deepseek_chat: BaseLLMClient,
        deepseek_reasoner: BaseLLMClient,
        local_model: BaseLLMClient
    ):
        self._config = config
        self._cache = cache_manager
        self._ds_chat = deepseek_chat
        self._ds_reasoner = deepseek_reasoner
        self._local = local_model

    async def route_and_execute(
        self,
        prompt: str,
        task_name: str,
        conviction_score: int = 0,
        has_anomalies: bool = False,
        max_tokens: int = 1000
    ) -> LLMResponse:
        """Route the task, checking cache first."""
        from atlas.telemetry.langfuse_client import telemetry
        span = telemetry.span(
            name="complexity_routing",
            input={
                "task_name": task_name,
                "conviction_score": conviction_score,
                "has_anomalies": has_anomalies,
                "prompt_length": len(prompt),
            },
        )
        try:
            cached = await self._cache.lookup(prompt, task_name)
            if cached:
                span.end(output="cache_hit", metadata={"tier": cached.tier})
                return _build_cached_response(cached, task_name)

            return await self._execute_and_cache(
                prompt, task_name, conviction_score, has_anomalies, max_tokens, span,
            )
        except Exception as e:
            span.update(level="ERROR", status_message=str(e))
            span.end()
            raise

    async def _execute_and_cache(
        self, prompt: str, task_name: str,
        conviction_score: int, has_anomalies: bool,
        max_tokens: int, span: Any,
    ) -> LLMResponse:
        """Select LLM client based on Tier, execute, cache, and return response."""
        tier, route_reason = self._get_tier(task_name, conviction_score, has_anomalies)
        logger.info(
            "Routing task to LLM | task={} | tier={} | reason={} | conviction={} | anomalies={}",
            task_name, tier, route_reason, conviction_score, has_anomalies,
        )
        span.update(metadata={"tier": tier, "route_reason": route_reason})

        if tier == "MANDATORY":
            client = self._ds_reasoner
            span.update(metadata={"provider": client.__class__.__name__})
            response = await client.complete(prompt, max_tokens=max_tokens)
        elif tier == "FAST":
            client = self._local if self._config.local_enabled else self._ds_chat
            span.update(metadata={"provider": client.__class__.__name__})
            response = await client.complete(prompt, max_tokens=max_tokens)
        else: # GATED
            client = self._local if self._config.local_enabled else self._ds_chat
            span.update(metadata={"provider": client.__class__.__name__})
            response = await client.complete(prompt, max_tokens=max_tokens)
            
            # Check confidence
            confidence = self._parse_confidence(response.text)
            if confidence < self._config.router_gated_confidence_threshold:
                logger.info("GATED tier escalated to reasoner | confidence={}", confidence)
                span.update(metadata={"escalated": True, "gated_confidence": confidence, "provider": self._ds_reasoner.__class__.__name__})
                client = self._ds_reasoner
                response = await client.complete(prompt, max_tokens=max_tokens)
            else:
                span.update(metadata={"escalated": False, "gated_confidence": confidence})

        await self._cache.store(prompt, response.text, provider=response.model, category=task_name, ttl=3600)
        span.end(output=response.text)
        return response

    def _get_tier(
        self,
        task_name: str,
        conviction_score: int,
        has_anomalies: bool
    ) -> tuple[str, str]:
        """Determine routing tier based on 3-Tier logic."""
        if task_name in HIGH_STAKES_TASKS:
            return "MANDATORY", "high_stakes_task_type"

        if has_anomalies and self._config.router_escalation_on_anomaly:
            return "MANDATORY", "anomaly_escalation"

        if conviction_score >= self._config.router_mandatory_threshold:
            return "MANDATORY", "conviction_score_mandatory"

        if conviction_score >= self._config.router_gated_threshold:
            return "GATED", "conviction_score_gated"

        return "FAST", "conviction_score_fast"

    def _parse_confidence(self, text: str) -> float:
        """Parse confidence percentage from LLM response text."""
        import re
        
        # Look for patterns like "85% confident"
        match = re.search(r'(\d+(?:\.\d+)?)%\s*confiden', text.lower())
        if match:
            return float(match.group(1)) / 100.0
            
        # Look for patterns like "confidence: 0.85"
        match = re.search(r'confidence[:\s]+(0\.\d+|1\.0)', text.lower())
        if match:
            return float(match.group(1))
            
        # Default to 0.0 to force escalation if we can't find confidence
        return 0.0
