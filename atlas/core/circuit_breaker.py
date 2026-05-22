"""Provider circuit breaker for ATLAS external HTTP calls.

Implements an async Redis-backed custom sliding window circuit breaker.
"""

from __future__ import annotations

import functools
import time
from typing import Any, Callable, TypeVar, cast

import msgspec
import redis.asyncio as redis_async
from loguru import logger

from atlas.shared.config import PolarisSettings

T = TypeVar("T")

STATE_CLOSED = "closed"
STATE_DEGRADED = "degraded"
STATE_OPEN = "open"
STATE_HALF_OPEN = "half_open"

class ProviderCircuitBreaker:
    """Async sliding window circuit breaker with Redis-backed state."""

    def __init__(
        self,
        provider_name: str,
        fail_max: int = 5,
        degraded_max: int = 5,
        window_size: int = 20,
        reset_timeout_seconds: float = 30.0,
    ) -> None:
        self.provider_name = provider_name
        self.fail_max = fail_max
        self.degraded_max = degraded_max
        self.window_size = window_size
        self.reset_timeout = reset_timeout_seconds
        
        settings = PolarisSettings()  # type: ignore[call-arg]
        self._redis = redis_async.from_url(settings.redis_url, socket_timeout=5.0)
        
        self._k_state = f"cb:{provider_name}:state"
        self._k_opened = f"cb:{provider_name}:opened_at"
        self._k_window = f"cb:{provider_name}:window"
        
        self.current_state = STATE_CLOSED

    async def _emit_transition(self, old_state: str, new_state: str) -> None:
        if old_state != new_state:
            logger.info(
                "circuit_transition | provider={} | status={}",
                self.provider_name,
                new_state
            )
            from atlas.telemetry.langfuse_client import telemetry
            telemetry.event(
                name="circuit_breaker_transition",
                input={
                    "provider": self.provider_name,
                    "old_state": old_state,
                    "new_state": new_state
                }
            )

    async def _evaluate_window(self) -> None:
        """Evaluate the sliding window and update state."""
        raw_window = await self._redis.lrange(self._k_window, 0, -1)  # type: ignore
        err_count = 0
        slow_count = 0
        for item in raw_window:
            try:
                data = msgspec.json.decode(item)
                if data.get("err"):
                    err_count += 1
                elif data.get("lat", 0) > 3.0:
                    slow_count += 1
            except Exception as e:
                logger.warning("circuit_breaker_decode_error | error={}", str(e))
                
        old_state = self.current_state
        
        if err_count >= self.fail_max:
            self.current_state = STATE_OPEN
            await self._redis.set(self._k_opened, time.time())
            await self._redis.set(self._k_state, STATE_OPEN)
            await self._redis.delete(self._k_window) # Clear window on open
        elif slow_count >= self.degraded_max:
            self.current_state = STATE_DEGRADED
            await self._redis.set(self._k_state, STATE_DEGRADED)
        else:
            self.current_state = STATE_CLOSED
            await self._redis.set(self._k_state, STATE_CLOSED)
            
        await self._emit_transition(old_state, self.current_state)

    async def trigger_latency_violation(self) -> None:
        """Trigger LATENCY_VIOLATION for this stage."""
        if self.current_state == STATE_DEGRADED:
            return
            
        old_state = self.current_state
        self.current_state = STATE_DEGRADED
        await self._redis.set(self._k_state, STATE_DEGRADED)
        
        logger.error(
            "LATENCY_VIOLATION | stage={} | action=degrade",
            self.provider_name
        )
        
        try:
            from atlas.telemetry.langfuse_client import telemetry
            telemetry.event(
                name="latency_violation",
                input={"stage": self.provider_name, "action": "degrade"}
            )
        except Exception as e:
            logger.warning("telemetry_event_failed | error={}", str(e))
            
        logger.info("TELEGRAM_ALERT: Stage {} LATENCY_VIOLATION", self.provider_name)
        await self._emit_transition(old_state, STATE_DEGRADED)

    async def resolve_latency_violation(self) -> None:
        """Resolve LATENCY_VIOLATION and restore full pipeline."""
        if self.current_state == STATE_CLOSED:
            return
            
        old_state = self.current_state
        self.current_state = STATE_CLOSED
        await self._redis.set(self._k_state, STATE_CLOSED)
        
        logger.info(
            "LATENCY_RECOVERY | stage={} | action=restore",
            self.provider_name
        )
        
        try:
            from atlas.telemetry.langfuse_client import telemetry
            telemetry.event(
                name="latency_recovery",
                input={"stage": self.provider_name, "action": "restore"}
            )
        except Exception as e:
            logger.warning("telemetry_event_failed | error={}", str(e))
            
        await self._emit_transition(old_state, STATE_CLOSED)

    async def call(self, func: Callable[..., Any], is_critical: bool, *args: Any, **kwargs: Any) -> Any:
        """Execute async function through the circuit breaker."""
        s = await self._redis.get(self._k_state)
        self.current_state = s.decode() if s else STATE_CLOSED
        if self.current_state == STATE_OPEN:
            o = await self._redis.get(self._k_opened)
            opened_at = float(o) if o else 0.0
            if time.time() - opened_at >= self.reset_timeout:
                self.current_state = STATE_HALF_OPEN
                await self._redis.set(self._k_state, STATE_HALF_OPEN)
            else:
                return None
        if self.current_state == STATE_DEGRADED and not is_critical:
            return None
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            await self._handle_call_success(start_time)
            return result
        except Exception as e:
            await self._handle_call_failure(start_time)
            raise e

    async def _handle_call_success(self, start_time: float) -> None:
        """Record successful call in the sliding window."""
        latency = time.time() - start_time
        if self.current_state == STATE_HALF_OPEN:
            self.current_state = STATE_CLOSED
            await self._redis.set(self._k_state, STATE_CLOSED)
            await self._emit_transition(STATE_HALF_OPEN, STATE_CLOSED)
        else:
            pipe = self._redis.pipeline()
            pipe.lpush(self._k_window, msgspec.json.encode({"ts": start_time, "lat": latency, "err": False}))
            pipe.ltrim(self._k_window, 0, self.window_size - 1)
            await pipe.execute()
            await self._evaluate_window()

    async def _handle_call_failure(self, start_time: float) -> None:
        """Record failed call in the sliding window."""
        latency = time.time() - start_time
        if self.current_state == STATE_HALF_OPEN:
            self.current_state = STATE_OPEN
            await self._redis.set(self._k_opened, time.time())
            await self._redis.set(self._k_state, STATE_OPEN)
            await self._emit_transition(STATE_HALF_OPEN, STATE_OPEN)
        else:
            pipe = self._redis.pipeline()
            pipe.lpush(self._k_window, msgspec.json.encode({"ts": start_time, "lat": latency, "err": True}))
            pipe.ltrim(self._k_window, 0, self.window_size - 1)
            await pipe.execute()
            await self._evaluate_window()

    async def hydrate_state_from_redis(self) -> None:
        """Load persisted breaker state into ``current_state`` without executing a call."""
        raw = await self._redis.get(self._k_state)
        if isinstance(raw, (bytes, bytearray)):
            self.current_state = raw.decode()
        elif isinstance(raw, str):
            self.current_state = raw
        else:
            self.current_state = STATE_CLOSED


_breakers: dict[str, ProviderCircuitBreaker] = {}

def get_circuit_breaker(provider_name: str) -> ProviderCircuitBreaker:
    if provider_name not in _breakers:
        _breakers[provider_name] = ProviderCircuitBreaker(provider_name)
    return _breakers[provider_name]

def with_circuit_breaker(provider_name: str, is_critical: bool = True) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            breaker = get_circuit_breaker(provider_name)
            return await breaker.call(func, is_critical, *args, **kwargs)
        return cast(Callable[..., T], wrapper)
    return decorator
