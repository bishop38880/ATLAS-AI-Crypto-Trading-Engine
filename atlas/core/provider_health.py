"""Provider health tracker.

Tracks success rates, latencies, and computes health scores and adaptive
timeouts for all data providers.
"""

from __future__ import annotations

import msgspec
import redis.asyncio as redis_async



class ProviderHealthState(msgspec.Struct, frozen=True):
    """Schema for stored health state."""

    health_score: float
    ema_latency: float
    adaptive_timeout: float


class RequestRecord(msgspec.Struct, frozen=True):
    """Schema for a single request record in the rolling window."""

    success: bool
    latency: float


class ProviderHealthTracker:
    """Tracks provider health and computes adaptive timeouts."""

    EXPECTED_LATENCIES_MS = {
        "coinalyze": 100.0,
        "pyth": 50.0,
        "hydra": 50.0,
        "defillama": 200.0,
        "nansen": 300.0,
        "fred": 500.0,
        "coinapi": 200.0,
    }

    def __init__(self, redis_client: redis_async.Redis) -> None:  # type: ignore[type-arg]
        """Initialize the health tracker."""
        self._redis = redis_client

    async def record_request(self, provider: str, success: bool, latency: float) -> None:
        """Record a request and update health scores."""
        record_bytes = msgspec.json.encode(RequestRecord(success, latency))
        list_key = f"provider:{provider}:requests"
        health_key = f"provider:{provider}:health_score"
        
        pipe = self._redis.pipeline()
        pipe.lpush(list_key, record_bytes)
        pipe.ltrim(list_key, 0, 99)
        pipe.lrange(list_key, 0, -1)
        pipe.get(health_key)
        
        results = await pipe.execute()
        raw_records, raw_health = results[2], results[3]
        
        prev = msgspec.json.decode(raw_health, type=ProviderHealthState) if raw_health else None
        ema = (0.1 * latency) + (0.9 * prev.ema_latency) if prev else latency
        timeout = max(2.0, min(30.0, ema * 3.0))
        score = self._compute_health_score(provider, raw_records)

        self._check_degradation(provider, score, prev.health_score if prev else 1.0, ema)

        state = ProviderHealthState(score, ema, timeout)
        await self._redis.set(health_key, msgspec.json.encode(state))

    def _compute_health_score(
        self, provider: str, raw_records: list[bytes],
    ) -> float:
        """Compute health score from rolling request records."""
        expected = self.EXPECTED_LATENCIES_MS.get(provider, 200.0) / 1000.0
        threshold = expected * 2.0

        succ_count = sum(1 for r in raw_records if msgspec.json.decode(r, type=RequestRecord).success)
        pen_count = sum(1 for r in raw_records if msgspec.json.decode(r, type=RequestRecord).latency > threshold)

        total = len(raw_records)
        sr = succ_count / total if total > 0 else 1.0
        lp = pen_count / total if total > 0 else 0.0
        return max(0.0, min(1.0, (0.7 * sr) + (0.3 * (1.0 - lp))))

    def _check_degradation(
        self, provider: str, score: float, prev_score: float, ema: float,
    ) -> None:
        """Emit telemetry event on health degradation."""
        if score < 0.8 and prev_score >= 0.8:
            from atlas.telemetry.langfuse_client import telemetry
            telemetry.event(
                name="provider_health_degraded",
                input={
                    "provider": provider,
                    "old_score": prev_score,
                    "new_score": score,
                    "ema_latency": ema,
                },
            )

    async def get_health(self, provider: str) -> float:
        """Get the current health score for a provider."""
        raw = await self._redis.get(f"provider:{provider}:health_score")
        if raw:
            return msgspec.json.decode(raw, type=ProviderHealthState).health_score
        return 1.0

    async def get_timeout(self, provider: str) -> float:
        """Get the current adaptive timeout for a provider."""
        raw = await self._redis.get(f"provider:{provider}:health_score")
        if raw:
            return msgspec.json.decode(raw, type=ProviderHealthState).adaptive_timeout
        return 10.0
