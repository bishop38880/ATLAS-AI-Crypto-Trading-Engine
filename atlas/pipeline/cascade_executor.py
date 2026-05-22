"""CascadeSetupDetector — two-stage probe-and-commit cascade setup detector.

ATLAS detects setups; PROMETHEUS executes orders. ATLAS publishes two
separate signal events to ``polaris:signals:{asset}`` — one per stage.

Liquidation cluster data comes from HYDRA (Redis DB 1, ``hydra:`` prefix).
CoinGlass has been fully removed from the stack.

Stage 1 (PROBE — 25 %): fires when all three preconditions are
simultaneously met.  Stage 2 (COMMIT — 75 %): fires when current price
touches the liquidation cluster identified at Stage 1.  No cooldown —
unlimited re-entry by design.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, ConfigDict

from atlas.models.signal import SignalOutput
from atlas.pipeline.pubsub import RedisSignalPublisher
from atlas.shared.config import PolarisSettings
from atlas.shared.hydra_asset import hydra_base_asset

_PROBE_TTL_HOURS = 4
_PROBE_TTL_SECONDS = _PROBE_TTL_HOURS * 3600
_CLUSTER_PROXIMITY_PCT = Decimal("0.03")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CascadeProbe(BaseModel):
    """Active Stage 1 cascade probe."""

    model_config = ConfigDict(frozen=True)
    asset: str
    stage1_entry_price: Decimal
    liquidation_cluster_price: Decimal
    stage1_signal_score: int
    created_at: datetime
    expires_at: datetime


class CascadeEvaluation(BaseModel):
    """Result of cascade evaluation for a cycle."""

    model_config = ConfigDict(frozen=True)
    asset: str
    action: Literal["STAGE1_FIRED", "STAGE2_FIRED", "PROBE_EXPIRED", "NO_ACTION"]
    probe_active: bool
    preconditions_met: dict[str, bool]
    emitted_signal_id: str | None
    cycle_timestamp: datetime


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class CascadeSetupDetector:
    """Manages two-stage probe-and-commit cascade setups.

    Args:
        settings: PolarisSettings instance.
        redis_client: Async Redis connection (ATLAS DB 0).
        signal_publisher: Thin pub/sub wrapper.
        hydra_redis_client: Async Redis connection (HYDRA DB 1).
    """

    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
        signal_publisher: RedisSignalPublisher,
        hydra_redis_client: redis_async.Redis,  # type: ignore[type-arg]
    ) -> None:
        self._settings = settings
        self._redis = redis_client
        self._publisher = signal_publisher
        self._hydra = hydra_redis_client

    # --- Precondition helpers ---

    def _check_precondition_a(self, signal: SignalOutput) -> bool:
        """Check if signal score is strong enough (≥ 82)."""
        return signal.score >= 82

    async def _check_precondition_b(
        self, asset: str, current_price: Decimal
    ) -> tuple[bool, Decimal | None]:
        """Check if a HYDRA liquidation cluster is within 3 % of price.

        Returns:
            (met, nearest_cluster_price) — reads ``hydra:clusters:{BASE}`` (HYDRA native asset).
        """
        suffix = hydra_base_asset(asset)
        try:
            raw = await asyncio.wait_for(
                self._hydra.get(f"hydra:clusters:{suffix}"),
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning("hydra cluster read failed | asset={} | err={}", asset, str(exc))
            return False, None

        if not raw:
            return False, None

        try:
            clusters_data = msgspec.json.decode(raw)
            return _find_nearest_cluster(clusters_data, current_price)
        except Exception as exc:
            logger.warning("hydra cluster decode failed | asset={} | err={}", asset, str(exc))
            return False, None

    def _check_precondition_c(self, signal: SignalOutput) -> bool:
        """Check if regime is trending (not ranging / high-volatility)."""
        if signal.category_scores.regime > 0:
            return True
        paths_str = " ".join(signal.contributing_graph_paths).lower()
        return "regime" in paths_str

    # --- Probe lifecycle ---

    async def _read_probe(self, asset: str) -> CascadeProbe | None:
        """Read active probe from Redis."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get(f"cascade:{asset}:probe"), timeout=5.0
            )
            if not raw:
                return None
            decoded = msgspec.json.decode(raw)
            return CascadeProbe.model_validate(decoded)
        except Exception as exc:
            logger.warning("redis degraded | op={} | asset={} | err={}", "read_probe", asset, str(exc))
            return None

    async def _invalidate_probe(self, asset: str, reason: str) -> None:
        """Delete probe from Redis and log invalidation."""
        try:
            await asyncio.wait_for(
                self._redis.delete(f"cascade:{asset}:probe"), timeout=5.0
            )
            logger.info("cascade probe invalidated | asset={} | reason={}", asset, reason)
        except Exception as exc:
            logger.error("redis delete probe failed | asset={} | err={}", asset, str(exc))

    # --- Signal emission ---

    async def _emit_stage1_signal(
        self, asset: str, signal: SignalOutput, probe: CascadeProbe
    ) -> str:
        """Publish Stage 1 cascade signal. Returns signal_id."""
        sig_id = _build_signal_id(probe.created_at, asset, "S1")
        signal_dict = signal.model_dump(mode="json")
        signal_dict.update({
            "stage": 1,
            "size_pct": 0.25,
            "probe_metadata": probe.model_dump(mode="json"),
            "signal_id": sig_id,
        })
        await self._publisher.publish(f"polaris:signals:{asset}", signal_dict)
        return sig_id

    async def _emit_stage2_signal(
        self, asset: str, probe: CascadeProbe, current_price: Decimal
    ) -> str:
        """Publish Stage 2 cascade signal. Returns signal_id."""
        ts = datetime.now(timezone.utc)
        sig_id = _build_signal_id(ts, asset, "S2")
        signal_dict: dict[str, Any] = {
            "stage": 2,
            "size_pct": 0.75,
            "original_stage1_signal_id": _build_signal_id(probe.created_at, asset, "S1"),
            "signal_id": sig_id,
            "timestamp": ts.isoformat(),
            "asset": asset,
            "current_price": str(current_price),
        }
        await self._publisher.publish(f"polaris:signals:{asset}", signal_dict)
        return sig_id

    # --- Core evaluation ---

    async def evaluate_cascade(
        self,
        asset: str,
        signal: SignalOutput,
        current_price: Decimal,
        liquidation_cluster_price: Decimal | None,
    ) -> CascadeEvaluation:
        """Evaluate two-stage cascade preconditions and triggers."""
        cycle_ts = datetime.now(timezone.utc)
        probe = await self._read_probe(asset)

        if probe:
            return await self._evaluate_stage2(asset, probe, current_price, cycle_ts)

        return await self._evaluate_stage1(
            asset, signal, current_price, liquidation_cluster_price, cycle_ts
        )

    async def _evaluate_stage2(
        self, asset: str, probe: CascadeProbe, current_price: Decimal, cycle_ts: datetime
    ) -> CascadeEvaluation:
        """Evaluate existing probe against Stage 2 conditions."""
        if cycle_ts > probe.expires_at:
            await self._invalidate_probe(asset, "EXPIRED")
            return CascadeEvaluation(
                asset=asset, action="PROBE_EXPIRED", probe_active=False,
                preconditions_met={}, emitted_signal_id=None, cycle_timestamp=cycle_ts
            )

        if current_price <= probe.liquidation_cluster_price:
            sig_id = await self._emit_stage2_signal(asset, probe, current_price)
            await self._invalidate_probe(asset, "STAGE2_FIRED")
            return CascadeEvaluation(
                asset=asset, action="STAGE2_FIRED", probe_active=False,
                preconditions_met={}, emitted_signal_id=sig_id, cycle_timestamp=cycle_ts
            )

        return CascadeEvaluation(
            asset=asset, action="NO_ACTION", probe_active=True,
            preconditions_met={}, emitted_signal_id=None, cycle_timestamp=cycle_ts
        )

    async def _evaluate_stage1(
        self, asset: str, signal: SignalOutput, current_price: Decimal,
        liquidation_cluster_price: Decimal | None, cycle_ts: datetime
    ) -> CascadeEvaluation:
        """Evaluate signal against Stage 1 preconditions."""
        prec_a = self._check_precondition_a(signal)
        prec_b_result = await self._check_precondition_b(asset, current_price)
        prec_b, nearest_cluster = prec_b_result
        prec_c = self._check_precondition_c(signal)

        preconds = {"A": prec_a, "B": prec_b, "C": prec_c}

        # Use HYDRA-discovered cluster if available, else caller-supplied
        cluster = nearest_cluster or liquidation_cluster_price

        if all(preconds.values()) and cluster is not None:
            return await self._store_and_emit_probe(
                asset, signal, current_price, cluster, preconds, cycle_ts
            )

        return CascadeEvaluation(
            asset=asset, action="NO_ACTION", probe_active=False,
            preconditions_met=preconds, emitted_signal_id=None, cycle_timestamp=cycle_ts
        )

    async def _store_and_emit_probe(
        self, asset: str, signal: SignalOutput, current_price: Decimal,
        cluster_price: Decimal, preconds: dict[str, bool], cycle_ts: datetime
    ) -> CascadeEvaluation:
        """Create probe, store in Redis, and emit Stage 1 signal."""
        new_probe = CascadeProbe(
            asset=asset,
            stage1_entry_price=current_price,
            liquidation_cluster_price=cluster_price,
            stage1_signal_score=signal.score,
            created_at=cycle_ts,
            expires_at=cycle_ts + timedelta(hours=_PROBE_TTL_HOURS),
        )
        try:
            payload = msgspec.json.encode(new_probe.model_dump(mode="json"))
            await asyncio.wait_for(
                self._redis.setex(f"cascade:{asset}:probe", _PROBE_TTL_SECONDS, payload),
                timeout=5.0,
            )
        except Exception as exc:
            logger.error("redis write probe failed | asset={} | err={}", asset, str(exc))
            return CascadeEvaluation(
                asset=asset, action="NO_ACTION", probe_active=False,
                preconditions_met=preconds, emitted_signal_id=None, cycle_timestamp=cycle_ts
            )

        sig_id = await self._emit_stage1_signal(asset, signal, new_probe)
        return CascadeEvaluation(
            asset=asset, action="STAGE1_FIRED", probe_active=True,
            preconditions_met=preconds, emitted_signal_id=sig_id, cycle_timestamp=cycle_ts
        )


# Backwards-compatible alias for downstream imports.
CascadeExecutor = CascadeSetupDetector


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _build_signal_id(ts: datetime, asset: str, stage: str) -> str:
    """Build deterministic signal ID with 8-char SHA suffix."""
    raw = f"{int(ts.timestamp())}_{asset}_{stage}"
    sha = hashlib.sha256(raw.encode()).hexdigest()[:8]
    return f"{raw}_{sha}"


def _find_nearest_cluster(
    clusters: list[dict[str, Any]], current_price: Decimal
) -> tuple[bool, Decimal | None]:
    """Find nearest cluster within 3 % of current price."""
    nearest: Decimal | None = None
    min_dist = Decimal("Infinity")

    for cluster in clusters:
        try:
            cluster_price = Decimal(str(cluster.get("price_band", "0")))
        except Exception:
            continue
        dist = abs(current_price - cluster_price)
        if dist < min_dist:
            min_dist = dist
            nearest = cluster_price

    if nearest is None:
        return False, None

    threshold = current_price * _CLUSTER_PROXIMITY_PCT
    return min_dist <= threshold, nearest
