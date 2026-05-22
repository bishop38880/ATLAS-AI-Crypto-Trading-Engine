"""Latency Monitor for Phase 5 Runtime SLA."""

import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis_async
from loguru import logger

from atlas.shared.config import PolarisSettings
from atlas.core.circuit_breaker import get_circuit_breaker


class LatencyMonitor:
    """Tracks pipeline stage latencies and enforces runtime SLAs."""

    def __init__(self, redis_client: redis_async.Redis, settings: PolarisSettings | None = None) -> None:  # type: ignore[type-arg]
        self._redis = redis_client
        self._settings = settings or PolarisSettings()
        self.budgets = self._settings.latency_budgets

    @asynccontextmanager
    async def measure(self, stage_name: str) -> AsyncGenerator[None, None]:
        """Measure execution time of a pipeline stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            latency = time.perf_counter() - start
            key = f"latency:{stage_name}"
            pipe = self._redis.pipeline()
            pipe.lpush(key, str(latency))
            pipe.ltrim(key, 0, 49)
            await pipe.execute()

    async def check_and_trigger(self) -> None:
        """Check P99 latencies against budgets and trigger violations."""
        for stage, budget in self.budgets.items():
            key = f"latency:{stage}"
            raw = await self._redis.lrange(key, 0, 49)  # type: ignore[call-arg]
            if len(raw) < 5:
                continue

            latencies = [float(r) for r in raw if _is_float(r)]
            if not latencies:
                continue

            p99 = _calculate_p99(latencies)
            await self._evaluate_stage_latency(stage, p99, budget)

    async def _evaluate_stage_latency(self, stage: str, p99: float, budget: float) -> None:
        """Evaluate a single stage's latency against its budget."""
        violation_key = f"latency_violations:{stage}"
        recovery_key = f"latency_recovery:{stage}"
        breaker = get_circuit_breaker(stage)

        if p99 > budget * 1.5:
            viol_count = await self._redis.incr(violation_key)  # type: ignore[call-arg]
            await self._redis.delete(recovery_key)  # type: ignore[call-arg]
            if viol_count >= 5:
                await breaker.trigger_latency_violation()
        elif p99 < budget * 1.2:
            rec_count = await self._redis.incr(recovery_key)  # type: ignore[call-arg]
            await self._redis.delete(violation_key)  # type: ignore[call-arg]
            if rec_count >= 10:
                await breaker.resolve_latency_violation()
        else:
            await self._redis.delete(violation_key)  # type: ignore[call-arg]
            await self._redis.delete(recovery_key)  # type: ignore[call-arg]

def _is_float(val: bytes | str) -> bool:
    try:
        float(val)
        return True
    except ValueError:
        return False

def _calculate_p99(latencies: list[float]) -> float:
    """Calculate the 99th percentile of a list of floats."""
    sorted_lats = sorted(latencies)
    idx = int(0.99 * (len(sorted_lats) - 1))
    return sorted_lats[idx]

