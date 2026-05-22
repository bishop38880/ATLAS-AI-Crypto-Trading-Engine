"""ShadowCollector — computes and stores four shadow metrics every cycle.

Architecture:
    Shadow metrics are tracked-only — they NEVER contribute to the
    220-point confluence score. The ``contributing_to_score`` field
    is hard-asserted False at runtime.

    Metrics:
        1. VWAP Deviation — overextension/undervaluation detection.
        2. Order Book Imbalance — persistent bid/ask pressure.
        3. Support/Resistance Proximity — ATR-normalised distance.
        4. ATR-Normalised Move Magnitude — unusual price moves.

    Data flows downstream to:
        - PostgreSQL ``shadow_metrics`` table.
        - Redis ``shadow:{asset}:latest`` with 60s TTL.
        - Shadow-to-Live Alpha Tracker (S3-P11).

Invariants:
    - No Polars, no pandas — list[OHLCVCandle] + Decimal math.
    - Financial values are Decimal; dimensionless ratios are float.
    - Frozen Pydantic v2 for ShadowMetrics.
    - Loguru structured kwargs only.
    - 40-line function limit enforced.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from typing import Literal

import asyncpg
import msgspec
import redis.asyncio

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.models.ohlcv import OHLCVCandle
from atlas.shared.config import PolarisSettings


# ---------------------------------------------------------------------------
# ShadowMetrics — frozen Pydantic model
# ---------------------------------------------------------------------------


class ShadowMetrics(BaseModel, frozen=True):
    """Computed shadow metrics for a single asset cycle.

    All dimensionless ratios use float. Financial levels use Decimal.
    ``contributing_to_score`` is a hard constant — always False.
    """

    model_config = ConfigDict(frozen=True)

    asset: str
    cycle_timestamp: datetime
    vwap_deviation: float
    vwap_deviation_direction: Literal["ABOVE", "BELOW", "NEUTRAL"]
    ob_imbalance: float | None
    ob_imbalance_persistent: bool
    ob_potential_spoof_detected: bool
    sr_proximity_atr_normalised: float
    nearest_sr_level: Decimal
    nearest_sr_type: Literal["SUPPORT", "RESISTANCE", "NEUTRAL"]
    atr_normalised_move: float
    atr_14: Decimal
    contributing_to_score: bool = Field(default=False)


# ---------------------------------------------------------------------------
# SQL schema (applied lazily)
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shadow_metrics (
    id              BIGSERIAL PRIMARY KEY,
    asset           TEXT NOT NULL,
    cycle_ts        TIMESTAMPTZ NOT NULL,
    vwap_deviation  DOUBLE PRECISION,
    ob_imbalance    DOUBLE PRECISION,
    ob_persistent   BOOLEAN,
    sr_proximity    DOUBLE PRECISION,
    atr_move        DOUBLE PRECISION,
    atr_14          NUMERIC,
    raw_json        JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_shadow_asset_ts
ON shadow_metrics(asset, cycle_ts DESC);
"""

_INSERT_SQL = """
INSERT INTO shadow_metrics
    (asset, cycle_ts, vwap_deviation, ob_imbalance,
     ob_persistent, sr_proximity, atr_move, atr_14, raw_json)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""


# ---------------------------------------------------------------------------
# ShadowCollector
# ---------------------------------------------------------------------------


class ShadowCollector:
    """Compute and persist four shadow metrics per cycle.

    Reads OHLCV candles, orderbook snapshots, and S/R levels.
    Writes to PostgreSQL and Redis. Never modifies the scorer.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: redis.asyncio.Redis,  # type: ignore[type-arg]
        asyncpg_pool: asyncpg.Pool,
    ) -> None:
        self._settings = settings
        self._redis = redis_client
        self._pool = asyncpg_pool

    async def collect(
        self,
        asset: str,
        ohlcv_data: list[OHLCVCandle],
        orderbook_snapshots: list[dict],  # type: ignore[type-arg]
        sr_levels: list[Decimal],
        cycle_timestamp: datetime,
    ) -> ShadowMetrics:
        """Run all four metric computations and return results."""
        atr_14 = _calculate_atr_14(ohlcv_data)
        current_price = _latest_close(ohlcv_data)
        candle_open = _latest_open(ohlcv_data)

        vwap_dev, vwap_dir = self._calculate_vwap_deviation(ohlcv_data)
        ob_imb, ob_pers, ob_spoof = self._calculate_ob_imbalance(
            orderbook_snapshots,
        )
        sr_prox, sr_level, sr_type = self._calculate_sr_proximity(
            current_price, sr_levels, atr_14,
        )
        atr_move = self._calculate_atr_normalised_move(
            current_price, candle_open, atr_14,
        )

        metrics = ShadowMetrics(
            asset=asset,
            cycle_timestamp=cycle_timestamp,
            vwap_deviation=vwap_dev,
            vwap_deviation_direction=vwap_dir,
            ob_imbalance=ob_imb,
            ob_imbalance_persistent=ob_pers,
            ob_potential_spoof_detected=ob_spoof,
            sr_proximity_atr_normalised=sr_prox,
            nearest_sr_level=sr_level,
            nearest_sr_type=sr_type,
            atr_normalised_move=atr_move,
            atr_14=atr_14,
        )

        assert not metrics.contributing_to_score, (
            "Shadow metrics must never affect scoring"
        )

        return metrics

    # -----------------------------------------------------------------------
    # VWAP Deviation
    # -----------------------------------------------------------------------

    def _calculate_vwap_deviation(
        self,
        ohlcv: list[OHLCVCandle],
    ) -> tuple[float, Literal["ABOVE", "BELOW", "NEUTRAL"]]:
        """Compute VWAP deviation from last 50 candles.

        Returns (deviation_ratio, direction_label).
        If fewer than 10 candles, returns neutral.
        """
        if len(ohlcv) < 10:
            return (0.0, "NEUTRAL")

        window = ohlcv[-50:] if len(ohlcv) >= 50 else ohlcv
        return _vwap_deviation_from_window(window)

    # -----------------------------------------------------------------------
    # Order Book Imbalance
    # -----------------------------------------------------------------------

    def _calculate_ob_imbalance(
        self,
        snapshots: list[dict],  # type: ignore[type-arg]
    ) -> tuple[float | None, bool, bool]:
        """Compute order book imbalance with persistence filter.

        Returns (imbalance | None, is_persistent, potential_spoof).
        Requires 3 consecutive snapshots for persistence check.
        """
        if len(snapshots) < 3:
            return (None, False, False)

        imbalances = _compute_snapshot_imbalances(snapshots[-3:])
        return _classify_imbalance_persistence(imbalances)

    # -----------------------------------------------------------------------
    # Support/Resistance Proximity
    # -----------------------------------------------------------------------

    def _calculate_sr_proximity(
        self,
        current_price: Decimal,
        sr_levels: list[Decimal],
        atr_14: Decimal,
    ) -> tuple[float, Decimal, Literal["SUPPORT", "RESISTANCE", "NEUTRAL"]]:
        """Compute ATR-normalised distance to nearest S/R level.

        Returns (distance, nearest_level, type_label).
        Empty sr_levels → (999.0, current_price, NEUTRAL).
        """
        if not sr_levels:
            return (999.0, current_price, "NEUTRAL")

        if atr_14 <= Decimal("0"):
            return (999.0, current_price, "NEUTRAL")

        nearest = min(sr_levels, key=lambda s: abs(current_price - s))
        sr_type = _classify_sr_type(current_price, nearest)
        distance = float(abs(current_price - nearest) / atr_14)
        return (distance, nearest, sr_type)

    # -----------------------------------------------------------------------
    # ATR-Normalised Move
    # -----------------------------------------------------------------------

    def _calculate_atr_normalised_move(
        self,
        current_price: Decimal,
        candle_open: Decimal,
        atr_14: Decimal,
    ) -> float:
        """Compute ATR-normalised move magnitude since candle open."""
        if atr_14 <= Decimal("0"):
            return 0.0
        return float(abs(current_price - candle_open) / atr_14)

    # -----------------------------------------------------------------------
    # Persistence — PostgreSQL + Redis
    # -----------------------------------------------------------------------

    async def ensure_schema(self) -> None:
        """Create shadow_metrics table and index if not exists."""
        async with self._pool.acquire() as conn:
            await asyncio.wait_for(conn.execute(_CREATE_TABLE_SQL), timeout=10.0)
            await asyncio.wait_for(conn.execute(_CREATE_INDEX_SQL), timeout=10.0)

    async def store(self, metrics: ShadowMetrics) -> None:
        """Persist metrics to PostgreSQL and Redis.

        Both paths run concurrently. On any failure: log ERROR,
        do NOT raise. Timeouts prevent silent hangs.
        """
        results = await asyncio.gather(
            self._store_postgres(metrics),
            self._store_redis(metrics),
            return_exceptions=True,
        )
        _log_store_errors(metrics.asset, results)

    async def _store_postgres(self, metrics: ShadowMetrics) -> None:
        """Write metrics row to shadow_metrics table."""
        raw_json = msgspec.json.encode(
            metrics.model_dump(mode="json"),
        ).decode("utf-8")

        async with self._pool.acquire() as conn:
            await asyncio.wait_for(
                conn.execute(
                    _INSERT_SQL,
                    metrics.asset,
                    metrics.cycle_timestamp,
                    metrics.vwap_deviation,
                    metrics.ob_imbalance,
                    metrics.ob_imbalance_persistent,
                    metrics.sr_proximity_atr_normalised,
                    metrics.atr_normalised_move,
                    str(metrics.atr_14),
                    raw_json,
                ),
                timeout=5.0,
            )

    async def _store_redis(self, metrics: ShadowMetrics) -> None:
        """Write latest metrics to Redis with 60s TTL."""
        key = "shadow:{}:latest".format(metrics.asset)
        payload = msgspec.json.encode(
            metrics.model_dump(mode="json"),
        )
        await asyncio.wait_for(
            self._redis.setex(key, 60, payload),
            timeout=5.0,
        )


def _log_store_errors(
    asset: str,
    results: tuple[BaseException | None, ...],
) -> None:
    """Log any exceptions returned by asyncio.gather."""
    labels = ("postgres", "redis")
    for label, result in zip(labels, results):
        if isinstance(result, BaseException):
            event_name = "shadow_store_{}_failed".format(label)
            message = "{} | asset={{}} | err={{}}".format(event_name)
            logger.error(
                message,
                asset,
                str(result),
            )


# ---------------------------------------------------------------------------
# Pure helper functions — extracted for 40-line cap
# ---------------------------------------------------------------------------


def _calculate_atr_14(ohlcv: list[OHLCVCandle]) -> Decimal:
    """Compute ATR(14) from candle data.

    Uses Wilder's smoothing: first ATR is simple mean of 14 TRs,
    subsequent TRs use exponential smoothing.
    Falls back to Decimal("1") on insufficient data.
    """
    if len(ohlcv) < 2:
        return Decimal("1")

    trs = _compute_true_ranges(ohlcv)
    if not trs:
        return Decimal("1")

    return _smooth_atr(trs, period=14)


def _compute_true_ranges(ohlcv: list[OHLCVCandle]) -> list[Decimal]:
    """Compute true range for each candle (starting from index 1)."""
    trs: list[Decimal] = []
    for i in range(1, len(ohlcv)):
        prev_close = ohlcv[i - 1].close
        high = ohlcv[i].high
        low = ohlcv[i].low
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    return trs


def _smooth_atr(trs: list[Decimal], period: int = 14) -> Decimal:
    """Apply Wilder's smoothing to true ranges."""
    if len(trs) < period:
        return sum(trs) / Decimal(str(len(trs))) if trs else Decimal("1")

    atr = sum(trs[:period]) / Decimal(str(period))
    p = Decimal(str(period))
    for tr in trs[period:]:
        atr = (atr * (p - Decimal("1")) + tr) / p
    return atr


def _latest_close(ohlcv: list[OHLCVCandle]) -> Decimal:
    """Return close price of the most recent candle."""
    if not ohlcv:
        return Decimal("0")
    return ohlcv[-1].close


def _latest_open(ohlcv: list[OHLCVCandle]) -> Decimal:
    """Return open price of the most recent candle."""
    if not ohlcv:
        return Decimal("0")
    return ohlcv[-1].open


def _vwap_deviation_from_window(
    window: list[OHLCVCandle],
) -> tuple[float, Literal["ABOVE", "BELOW", "NEUTRAL"]]:
    """Compute VWAP deviation and direction from a candle window.

    typical_price = (high + low + close) / 3
    vwap = sum(tp * volume) / sum(volume)
    deviation = (latest_close - vwap) / vwap
    """
    sum_tp_vol = Decimal("0")
    sum_vol = Decimal("0")

    three = Decimal("3")
    for c in window:
        tp = (c.high + c.low + c.close) / three
        sum_tp_vol += tp * c.volume
        sum_vol += c.volume

    if sum_vol <= Decimal("0"):
        return (0.0, "NEUTRAL")

    vwap = sum_tp_vol / sum_vol
    if vwap <= Decimal("0"):
        return (0.0, "NEUTRAL")

    deviation = float((window[-1].close - vwap) / vwap)
    direction = _classify_deviation_direction(deviation)
    return (deviation, direction)


def _classify_deviation_direction(deviation: float) -> Literal["ABOVE", "BELOW", "NEUTRAL"]:
    """Map deviation to ABOVE/BELOW/NEUTRAL."""
    if deviation > 0.001:
        return "ABOVE"
    if deviation < -0.001:
        return "BELOW"
    return "NEUTRAL"


def _compute_snapshot_imbalances(
    snapshots: list[dict],  # type: ignore[type-arg]
) -> list[float]:
    """Compute bid/ask imbalance ratio for each snapshot."""
    imbalances: list[float] = []
    for snap in snapshots:
        if not snap:
            imbalances.append(0.0)
            continue
        bid_vol = float(snap.get("bid_vol_top10", 0))
        ask_vol = float(snap.get("ask_vol_top10", 0))
        total = bid_vol + ask_vol
        if total <= 0:
            imbalances.append(0.0)
        else:
            imbalances.append((bid_vol - ask_vol) / total)
    return imbalances


def _classify_imbalance_persistence(
    imbalances: list[float],
) -> tuple[float | None, bool, bool]:
    """Check if imbalance direction is consistent across all snapshots.

    Consistent → (mean_imbalance, True, False).
    Single spike → (None, False, True).
    """
    signs = [_sign(i) for i in imbalances]
    non_zero_signs = [s for s in signs if s != 0]

    if not non_zero_signs:
        return (None, False, False)

    all_same = all(s == non_zero_signs[0] for s in non_zero_signs)
    if all_same and len(non_zero_signs) == len(imbalances):
        mean_imb = sum(imbalances) / len(imbalances)
        return (mean_imb, True, False)

    return (None, False, True)


def _sign(x: float) -> int:
    """Return +1, -1, or 0."""
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _classify_sr_type(
    current_price: Decimal,
    nearest: Decimal,
) -> Literal["SUPPORT", "RESISTANCE", "NEUTRAL"]:
    """Classify S/R level relative to current price."""
    if nearest < current_price:
        return "SUPPORT"
    if nearest > current_price:
        return "RESISTANCE"
    return "NEUTRAL"
