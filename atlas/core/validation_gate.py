"""ValidationGate — 5-layer data validation pipeline.

1. Schema Validation (Pydantic)
2. Adaptive Staleness (3 * P90 interval)
3. Anomaly Detection (Isolation Forest)
4. Cross-source Consistency
5. KS Drift Detection (distribution poisoning)
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import TypeVar, Type, Any

import msgspec
import numpy as np
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, ValidationError

from atlas.core.anomaly_detector import AnomalyDetector
from atlas.core.consistency_checker import ConsistencyChecker

T = TypeVar("T", bound=BaseModel)


class ValidationGate:
    """Orchestrates the 4-layer validation pipeline for inbound provider data."""

    def __init__(self, redis_client: redis_async.Redis) -> None:
        self._redis = redis_client
        self.anomaly_detector = AnomalyDetector(redis_client)
        self.consistency_checker = ConsistencyChecker()

    async def _update_and_get_p90_interval(self, provider: str, current_time: float) -> float | None:
        """Track update intervals and compute P90 for adaptive staleness."""
        last_key = f"staleness:{provider}:last_time"
        buffer_key = f"staleness:{provider}:intervals"

        # Get last update time
        last_time_raw = await self._redis.get(last_key)
        
        # Set new update time
        await self._redis.set(last_key, current_time)

        if not last_time_raw:
            return None

        last_time = float(last_time_raw)
        interval = current_time - last_time

        # Ignore wildly large intervals (e.g. restarts)
        if interval > 3600:
            return None

        async with self._redis.pipeline() as pipe:
            pipe.rpush(buffer_key, interval)
            pipe.ltrim(buffer_key, -100, -1)
            pipe.lrange(buffer_key, 0, -1)
            results = await pipe.execute()

        intervals = [float(x) for x in results[-1]]
        if len(intervals) < 10:
            return None

        return float(np.percentile(intervals, 90))

    async def validate(
        self,
        schema: Type[T],
        provider: str,
        asset: str,
        metric: str,
        value: Decimal,
        timestamp: float,
        source_timestamp: float,
        all_provider_values: dict[str, Decimal] | None = None,
        **kwargs: Any,
    ) -> T | None:
        """Run the validation pipeline on incoming data."""
        payload = {
            "provider": provider, "asset": asset, "metric": metric,
            "value": value, "timestamp": timestamp, "source_timestamp": source_timestamp, "validation_flags": [], **kwargs,
        }
        try:
            validated_model = schema(**payload)
        except ValidationError as e:
            logger.error("schema validation failed | error={}", str(e))
            _emit_event("validation_schema_failed", provider, asset, metric, error=str(e))
            return None

        if await self._is_stale(provider, timestamp):
            _emit_event("validation_stale", provider, asset, metric)
            return None

        flags, hard_block = await self._compute_flags(provider, asset, metric, value, all_provider_values)
        if hard_block:
            _emit_event("validation_hard_block", provider, asset, metric)
            logger.warning("hard block | provider={} | value={}", provider, value)
            return None

        if flags:
            _emit_event("validation_flags_attached", provider, asset, metric, flags=flags)
            return validated_model.model_copy(update={"validation_flags": flags})

        return validated_model

    async def _is_stale(self, provider: str, timestamp: float) -> bool:
        """Check if the update is stale based on P90 interval."""
        p90 = await self._update_and_get_p90_interval(provider, timestamp)
        if p90 is None:
            return False
            
        delay = time.time() - timestamp
        stale_threshold = max(3 * p90, 1.0)
        
        if delay > stale_threshold:
            logger.warning("stale | provider={} | delay={:.2f}s", provider, delay)
            return True
        return False

    async def _compute_flags(
        self, provider: str, asset: str, metric: str, value: Decimal,
        all_provider_values: dict[str, Decimal] | None
    ) -> tuple[list[str], bool]:
        """Run anomaly, consistency, and KS drift checks."""
        flags: list[str] = []
        hard_block = False
        
        anomaly_res = await self.anomaly_detector.check_anomaly(provider, asset, metric, float(value))
        if anomaly_res.is_anomaly:
            flags.append("ANOMALY_DETECTED")
        if anomaly_res.hard_block:
            hard_block = True

        # Update distribution and run KS drift after MAD checks
        await self._update_and_check_ks(provider, metric, float(value), flags)
            
        if all_provider_values:
            check_values = dict(all_provider_values)
            check_values[provider] = value
            cons_res = await self.consistency_checker.check_consistency(metric, check_values)
            if not cons_res.is_consistent:
                flags.append("CONSISTENCY_DIVERGENCE")
                
        return flags, hard_block

    async def _update_and_check_ks(
        self, provider: str, metric: str, value: float, flags: list[str],
    ) -> None:
        """Update distribution and run KS drift test."""
        try:
            await self.anomaly_detector.update_distribution(metric, provider, value)
            ks_result = await self.anomaly_detector.ks_drift_detected(metric, provider)
            if ks_result.drift_detected:
                flags.append("KS_DRIFT_DETECTED")
                await self._handle_ks_drift(provider, metric, ks_result)
        except Exception as exc:
            logger.warning("ks_drift_check_failed | provider={} | error={}", provider, str(exc))

    async def _handle_ks_drift(
        self, provider: str, metric: str, ks_result: Any,
    ) -> None:
        """Mark provider DEGRADED and send throttled alert on KS drift."""
        await self._redis.set(f"cb:{provider}:state", "degraded")
        logger.error(
            "KS_DRIFT_DETECTED | provider={} | field={} | ks={:.4f} | p={:.6f}",
            provider, metric, ks_result.ks_statistic, ks_result.p_value,
        )
        await self._send_throttled_ks_alert(provider, metric, ks_result)

    async def _send_throttled_ks_alert(
        self, provider: str, metric: str, ks_result: Any,
    ) -> None:
        """Send Telegram alert throttled to once per day per field."""
        throttle_key = f"ks_alert:{provider}:{metric}"
        if await self._redis.exists(throttle_key):
            return
        await self._redis.setex(throttle_key, 86400, "1")  # 24h TTL
        try:
            from scripts.telegram_alert import send_telegram_alert
            msg = (
                f"🚨 *KS Drift Detected*\n"
                f"Provider: `{provider}`\n"
                f"Field: `{metric}`\n"
                f"KS Statistic: `{ks_result.ks_statistic:.4f}`\n"
                f"p-value: `{ks_result.p_value:.6f}`"
            )
            await send_telegram_alert(msg)
        except Exception as exc:
            logger.warning("ks_drift_telegram_failed | error={}", str(exc))


def _emit_event(
    name: str, provider: str, asset: str, metric: str, **extra: Any,
) -> None:
    """Emit a Langfuse telemetry event for validation outcomes."""
    from atlas.telemetry.langfuse_client import telemetry
    payload = {"provider": provider, "asset": asset, "metric": metric, **extra}
    telemetry.event(name=name, input=payload)

