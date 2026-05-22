"""LLM Client Abstraction Layer.

Provides unified interface for querying DeepSeek and local models.
"""

from abc import ABC, abstractmethod
import time
from typing import Any

import httpx
import msgspec
import redis.asyncio as redis_async
from pydantic import BaseModel, Field

from atlas.core.monitoring_telemetry import record_llm_cost, safe_monitoring_write
from atlas.shared.config import ModelStackConfig


class LLMProviderError(Exception):
    """Exception raised for LLM API failures."""
    
    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


class LocalLLMUnavailableError(LLMProviderError):
    """Local inference (LM Studio) is disabled or unreachable."""

    pass


class LLMResponse(BaseModel):
    """Normalized response from any LLM provider."""
    
    text: str
    reasoning: str | None
    model: str
    latency_ms: int
    tokens_used: int
    cost_estimate_usd: float

    model_config = {"frozen": True}


class BaseLLMClient(ABC):
    """Abstract interface for LLM clients."""

    @abstractmethod
    async def complete(self, prompt: str, max_tokens: int = 1000) -> LLMResponse:
        """Send a prompt and return a normalized response."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the provider is available."""
        pass


class DeepSeekClient(BaseLLMClient):
    """Client for DeepSeek Chat and Reasoner APIs."""

    def __init__(
        self,
        config: ModelStackConfig,
        is_reasoner: bool = False,
        redis_client: redis_async.Redis | None = None,  # type: ignore[type-arg]
    ):
        self._config = config
        self._is_reasoner = is_reasoner
        self._redis = redis_client
        self._client = httpx.AsyncClient(timeout=self._config.deepseek_timeout_s)

    async def complete(self, prompt: str, max_tokens: int = 1000) -> LLMResponse:
        start_t = time.perf_counter()
        model_name = (self._config.deepseek_reasoner_model if self._is_reasoner 
                      else self._config.deepseek_chat_model)
        
        body = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
        
        from atlas.telemetry.langfuse_client import telemetry
        gen = telemetry.generation(name="deepseek_complete", model=model_name, input=prompt)
        
        try:
            resp = await self._send_request(body)
            data = msgspec.json.decode(resp.content)
            result = self._parse_response(data, model_name, start_t)
            self._record_generation_telemetry(gen, data, result)
            await self._record_cost(result)
            return result
        except Exception as e:
            gen.update(level="ERROR", status_message=str(e))
            raise

    def _record_generation_telemetry(
        self, gen: Any, data: dict[str, Any], result: LLMResponse,
    ) -> None:
        """End the Langfuse generation span with usage and metadata."""
        usage = data.get("usage", {})
        gen.end(
            output=result.text,
            usage={
                "input": usage.get("prompt_tokens", 0),
                "output": usage.get("completion_tokens", 0),
                "total": usage.get("total_tokens", 0),
            },
            metadata={
                "cost_estimate_usd": result.cost_estimate_usd,
                "latency_ms": result.latency_ms,
                "reasoning": result.reasoning,
                "is_reasoner": self._is_reasoner,
            },
        )

    async def _record_cost(self, result: LLMResponse) -> None:
        """Write DeepSeek cost telemetry when a Redis client is available."""
        if self._redis is None:
            return
        cap = getattr(self._config, "api_cost_cap_usd", None)
        await safe_monitoring_write(
            record_llm_cost(
                self._redis,
                provider=result.model,
                cost_usd=result.cost_estimate_usd,
                cost_cap_usd=cap,
            ),
            action="record_llm_cost",
        )

    async def _send_request(self, body: dict[str, Any]) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self._config.deepseek_api_key.get_secret_value()}",
            "Content-Type": "application/json"
        }
        try:
            resp = await self._client.post(
                f"{self._config.deepseek_base_url}/chat/completions",
                content=msgspec.json.encode(body),
                headers=headers
            )
            resp.raise_for_status()
            return resp
        except httpx.TimeoutException as e:
            raise LLMProviderError(f"DeepSeek timeout: {e}", retriable=True)
        except httpx.HTTPStatusError as e:
            raise LLMProviderError(f"DeepSeek HTTP error {e.response.status_code}: {e.response.text}", retriable=False)
        except Exception as e:
            raise LLMProviderError(f"DeepSeek connection error: {e}", retriable=True)

    def _parse_response(self, data: dict[str, Any], model_name: str, start_t: float) -> LLMResponse:
        message = data["choices"][0]["message"]
        usage = data.get("usage", {})
        prompt_t = usage.get("prompt_tokens", 0)
        comp_t = usage.get("completion_tokens", 0)
        
        # Rough cost estimate
        cost = (prompt_t * 0.00014 / 1000) + (comp_t * 0.00028 / 1000)
        
        return LLMResponse(
            text=message.get("content", ""),
            reasoning=message.get("reasoning_content"),
            model=model_name,
            latency_ms=int((time.perf_counter() - start_t) * 1000),
            tokens_used=usage.get("total_tokens", 0),
            cost_estimate_usd=cost
        )

    async def health_check(self) -> bool:
        try:
            # Send a minimal request to verify connectivity
            headers = {"Authorization": f"Bearer {self._config.deepseek_api_key.get_secret_value()}"}
            resp = await self._client.get(
                f"{self._config.deepseek_base_url}/models",
                headers=headers
            )
            return resp.status_code == 200
        except Exception:
            return False


class LocalLLMClient(BaseLLMClient):
    """Client for local llama.cpp endpoints."""

    def __init__(self, config: ModelStackConfig):
        self._config = config
        self._client = httpx.AsyncClient(timeout=self._config.local_timeout_s)

    async def complete_confluence_turn(
        self,
        *,
        system_prompt: str,
        user_message: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Bundle system instructions and payload for a single `/completion` request."""
        tokens = max_tokens if max_tokens is not None else self._config.local_max_tokens
        prompt = "".join([
            "### SYSTEM_INSTRUCTIONS\n",
            system_prompt,
            "\n\n### USER_PAYLOAD\n",
            user_message,
        ])
        return await self.complete(prompt, max_tokens=tokens)

    async def complete(self, prompt: str, max_tokens: int = 1000) -> LLMResponse:
        if not self._config.local_enabled:
            raise LocalLLMUnavailableError(
                "Local model is not enabled",
                retriable=False,
            )
            
        start_t = time.perf_counter()
        
        body = {
            "prompt": prompt,
            "n_predict": max_tokens,
        }

        try:
            resp = await self._client.post(
                f"{self._config.local_url}/completion",
                content=msgspec.json.encode(body),
                headers={"Content-Type": "application/json"}
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise LocalLLMUnavailableError(
                "Local model timeout: {}".format(e),
                retriable=True,
            )
        except Exception as e:
            raise LocalLLMUnavailableError(
                "Local model error: {}".format(e),
                retriable=True,
            )

        data = msgspec.json.decode(resp.content)
        text = data.get("content", "")
        tokens_used = data.get("tokens_evaluated", 0) + data.get("tokens_predicted", 0)
        latency_ms = int((time.perf_counter() - start_t) * 1000)

        return LLMResponse(
            text=text,
            reasoning=None,
            model=self._config.local_model_name,
            latency_ms=latency_ms,
            tokens_used=tokens_used,
            cost_estimate_usd=0.0
        )

    async def health_check(self) -> bool:
        if not self._config.local_enabled:
            return False
            
        try:
            resp = await self._client.get(f"{self._config.local_url}/health")
            return resp.status_code == 200
        except Exception:
            return False
