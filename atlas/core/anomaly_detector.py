"""AnomalyDetector — detects anomalies in data using Isolation Forest.

Maintains a rolling buffer in Redis and uses scikit-learn's Isolation Forest
(wrapped in asyncio.to_thread) for anomaly detection, falling back to Z-score.
Extended with KS drift detection for slow distribution poisoning.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import ClassVar, Any, Awaitable, cast

import msgspec
import redis.asyncio as redis_async
from pydantic import BaseModel, ConfigDict
from loguru import logger
import numpy as np
from scipy import stats
from sklearn.ensemble import IsolationForest


class AnomalyResult(BaseModel):
    """Immutable Pydantic model for anomaly results."""

    model_config = ConfigDict(frozen=True)

    is_anomaly: bool
    hard_block: bool
    anomaly_score: float
    mad_z_score: float
    rolling_median: float
    rolling_mad: float


class KsDriftResult(BaseModel):
    """Immutable result of KS drift test."""

    model_config = ConfigDict(frozen=True)

    drift_detected: bool
    ks_statistic: float
    p_value: float
    baseline_size: int
    recent_size: int


class AnomalyDetector:
    """Detects anomalies using a rolling buffer and Isolation Forest.
    
    Maintains a 200-point rolling buffer in Redis, retraining the forest
    every 50 points. Falls back to Z-score if under 30 points.
    Extended with KS drift detection for slow distribution poisoning.
    """

    MAX_BUFFER: ClassVar[int] = 200
    RETRAIN_INTERVAL: ClassVar[int] = 50
    MIN_POINTS_FOR_FOREST: ClassVar[int] = 30
    Z_SCORE_THRESHOLD: ClassVar[float] = 4.0
    KS_P_THRESHOLD: ClassVar[float] = 0.01
    DIST_RESERVOIR_SIZE: ClassVar[int] = 2000
    DIST_TTL_SECONDS: ClassVar[int] = 90 * 86400  # 90 days
    RECENT_WINDOW_SECONDS: ClassVar[int] = 7 * 86400  # 7 days
    MIN_SAMPLES_FOR_KS: ClassVar[int] = 30

    def __init__(self, redis_client: redis_async.Redis) -> None:
        self._redis: Any = redis_client
        self._forests: dict[str, IsolationForest] = {}
        self._reservoir_counts: dict[str, int] = {}

    async def _update_buffer(self, key: str, value: float) -> list[float]:
        """Update Redis buffer and return the latest elements."""
        encoded = msgspec.json.encode(value)
        async with self._redis.pipeline() as pipe:
            pipe.rpush(key, encoded)
            pipe.ltrim(key, -self.MAX_BUFFER, -1)
            pipe.lrange(key, 0, -1)
            results = await pipe.execute()
        
        raw_list = results[-1]
        return [float(msgspec.json.decode(item)) for item in raw_list]

    def _compute_mad_z_score(self, value: float, buffer: list[float]) -> tuple[float, float, float]:
        """Compute rolling median, MAD, and MAD Z-score."""
        if not buffer:
            return 0.0, float(value), 0.0
        median = float(np.median(buffer))
        mad = float(np.median(np.abs(np.array(buffer) - median)))
        if mad > 0:
            mad_z = float(0.6745 * (value - median) / mad)
        else:
            mad_z = 0.0
        return mad_z, median, mad

    def _train_forest(self, buffer: list[float]) -> IsolationForest:
        """Train and return an IsolationForest (must be run in thread)."""
        forest = IsolationForest(contamination=0.05, random_state=42)  # type: ignore[arg-type]
        # Reshape for sklearn: [[v1], [v2], ...]
        buffer_array = np.array(buffer).reshape(-1, 1)
        forest.fit(buffer_array)
        return forest

    def _predict_forest(self, forest: IsolationForest, value: float) -> tuple[bool, float]:
        """Predict anomaly using trained forest (must be run in thread)."""
        score = float(forest.decision_function([[value]])[0])
        # predict returns -1 for anomaly, 1 for inlier
        pred = forest.predict([[value]])[0]
        return bool(pred == -1), score

    async def check_anomaly(
        self, provider: str, asset: str, metric: str, value: float
    ) -> AnomalyResult:
        """Check if a new data point is an anomaly."""
        key = f"anomaly:{provider}:{asset}:{metric}"
        buffer = await self._update_buffer(key, value)
        mad_z, median, mad = self._compute_mad_z_score(value, buffer)
        flag_thresh, block_thresh = await self._get_thresholds()

        if len(buffer) < self.MIN_POINTS_FOR_FOREST:
            return self._check_zscore_only(mad_z, median, mad, flag_thresh, block_thresh)
        return await self._check_with_forest(
            key, buffer, value, mad_z, median, mad, flag_thresh, block_thresh,
        )

    async def _get_thresholds(self) -> tuple[float, float]:
        """Get anomaly thresholds based on volatility regime."""
        regime_raw = await self._redis.get("market:volatility_regime")
        is_volatile = regime_raw is not None and regime_raw.decode("utf-8") == "high_volatility"
        flag = 5.0 if is_volatile else 3.5
        block = 8.0 if is_volatile else 6.0
        return flag, block

    @staticmethod
    def _check_zscore_only(
        mad_z: float, median: float, mad: float,
        flag_thresh: float, block_thresh: float,
    ) -> AnomalyResult:
        """Fallback to MAD Z-score when insufficient points for forest."""
        is_anomaly = abs(mad_z) > flag_thresh
        hard_block = abs(mad_z) > block_thresh
        return AnomalyResult(
            is_anomaly=is_anomaly or hard_block, hard_block=hard_block,
            anomaly_score=0.0, mad_z_score=mad_z,
            rolling_median=median, rolling_mad=mad,
        )

    async def _check_with_forest(
        self, key: str, buffer: list[float], value: float,
        mad_z: float, median: float, mad: float,
        flag_thresh: float, block_thresh: float,
    ) -> AnomalyResult:
        """Run Isolation Forest check with MAD Z-score fusion."""
        forest = self._forests.get(key)
        if forest is None or len(buffer) % self.RETRAIN_INTERVAL == 0:
            forest = await asyncio.to_thread(self._train_forest, buffer)
            self._forests[key] = forest
        is_anomaly_forest, score = await asyncio.to_thread(self._predict_forest, forest, value)
        is_anomaly = is_anomaly_forest or abs(mad_z) > flag_thresh
        hard_block = abs(mad_z) > block_thresh
        return AnomalyResult(
            is_anomaly=is_anomaly or hard_block, hard_block=hard_block,
            anomaly_score=score, mad_z_score=mad_z,
            rolling_median=median, rolling_mad=mad,
        )

    # ── KS Drift Detection ──────────────────────────────────────────

    async def update_distribution(
        self, field_name: str, provider: str, value: float,
    ) -> None:
        """Update the rolling 90-day distribution with a new data point.

        Uses reservoir sampling to keep a bounded sample in Redis.
        """
        dist_key = f"dist:{field_name}:{provider}"
        recent_key = f"dist_recent:{field_name}:{provider}"
        now = time.time()
        entry = msgspec.json.encode({"v": value, "ts": now})

        await self._store_reservoir_sample(dist_key, entry, field_name, provider)
        await self._append_recent_sample(recent_key, entry, now)

    async def _store_reservoir_sample(
        self, dist_key: str, entry: bytes, field_name: str, provider: str,
    ) -> None:
        """Add entry to reservoir-sampled baseline distribution."""
        count_key = f"{dist_key}:count"
        raw_count = await self._redis.get(count_key)
        n = int(raw_count) if raw_count else 0
        n += 1
        await self._redis.set(count_key, n)

        if n <= self.DIST_RESERVOIR_SIZE:
            await self._redis.rpush(dist_key, entry)
            await self._redis.expire(dist_key, self.DIST_TTL_SECONDS)
        else:
            j = random.randint(1, n)
            if j <= self.DIST_RESERVOIR_SIZE:
                await self._redis.lset(dist_key, j - 1, entry)

        await self._redis.expire(count_key, self.DIST_TTL_SECONDS)

    async def _append_recent_sample(
        self, recent_key: str, entry: bytes, now: float,
    ) -> None:
        """Append to the recent-window sorted set, pruning old entries."""
        await self._redis.zadd(recent_key, {entry: now})
        cutoff = now - self.RECENT_WINDOW_SECONDS
        await self._redis.zremrangebyscore(recent_key, "-inf", cutoff)
        await self._redis.expire(recent_key, self.RECENT_WINDOW_SECONDS + 86400)

    async def ks_drift_detected(
        self, field_name: str, provider: str,
    ) -> KsDriftResult:
        """Run 2-sample KS test: recent 7 days vs 90-day baseline.

        Returns KsDriftResult with drift_detected=True if p < 0.01.
        """
        baseline = await self._retrieve_baseline(field_name, provider)
        recent = await self._retrieve_recent(field_name, provider)

        if len(baseline) < self.MIN_SAMPLES_FOR_KS or len(recent) < self.MIN_SAMPLES_FOR_KS:
            return KsDriftResult(
                drift_detected=False, ks_statistic=0.0, p_value=1.0,
                baseline_size=len(baseline), recent_size=len(recent),
            )

        result = await asyncio.to_thread(stats.ks_2samp, baseline, recent)
        return KsDriftResult(
            drift_detected=result.pvalue < self.KS_P_THRESHOLD,
            ks_statistic=float(result.statistic),
            p_value=float(result.pvalue),
            baseline_size=len(baseline),
            recent_size=len(recent),
        )

    async def _retrieve_baseline(
        self, field_name: str, provider: str,
    ) -> list[float]:
        """Retrieve baseline distribution values from Redis reservoir."""
        dist_key = f"dist:{field_name}:{provider}"
        raw = await self._redis.lrange(dist_key, 0, -1) # type: ignore
        return [msgspec.json.decode(item)["v"] for item in raw]

    async def _retrieve_recent(
        self, field_name: str, provider: str,
    ) -> list[float]:
        """Retrieve recent 7-day values from Redis sorted set."""
        recent_key = f"dist_recent:{field_name}:{provider}"
        raw = await self._redis.zrange(recent_key, 0, -1)
        return [msgspec.json.decode(item)["v"] for item in raw]
