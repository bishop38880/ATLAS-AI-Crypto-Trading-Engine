"""Shadow-to-Live Alpha Tracker — S3-P11.

Retrospective analysis: after each trade closes, simulate what the
confluence score *would have been* if each shadow metric had been scored
at its candidate weight. Accumulates empirical basis for future
human-reviewed promotion decisions.

CRITICAL RULE:
    AlphaTracker NEVER modifies confluence weights, scoring config, or
    agent files. It ONLY reads/writes to ``shadow_alpha_tracking``.

Architecture:
    - Frozen Pydantic v2 models for all outputs.
    - Loguru structured kwargs only.
    - 40-line function limit enforced.
    - Decimal for financial fields; float for dimensionless ratios.
    - msgspec for serialization.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

import asyncio

import asyncpg
import msgspec
import redis.asyncio

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.models.signal import SignalOutput, TradeOutcome
from atlas.shared.config import PolarisSettings
from shadow.collector import ShadowMetrics


# ---------------------------------------------------------------------------
# Candidate weight configuration — hypothetical, for simulation only
# ---------------------------------------------------------------------------

_CANDIDATE_WEIGHTS: dict[str, int] = {
    "vwap_deviation": 8,
    "ob_imbalance": 7,
    "sr_proximity": 6,
    "atr_normalised_move": 5,
}

_TOTAL_DENOMINATOR = 220


# ---------------------------------------------------------------------------
# Decision mapping — mirrors OutputProcessor._determine_decision exactly
# ---------------------------------------------------------------------------

DecisionLabel = Literal[
    "STRONG_BUY",
    "BUY",
    "HOLD",
    "NO_POSITION",
]


def _determine_decision(score: int) -> DecisionLabel:
    """Map normalised 0-100 score to a decision label.

    Mirrors OutputProcessor._determine_decision exactly.
    """
    if score >= 82:
        return "STRONG_BUY"
    if score >= 68:
        return "BUY"
    if score >= 55:
        return "HOLD"
    return "NO_POSITION"


_TRADEABLE_DECISIONS: frozenset[str] = frozenset({
    "STRONG_BUY", "BUY",
})


# ---------------------------------------------------------------------------
# Frozen Pydantic models
# ---------------------------------------------------------------------------


class MetricSimulation(BaseModel, frozen=True):
    """Simulation result for one shadow metric on one closed trade."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    candidate_weight: int
    shadow_pts_awarded: int
    shadow_adjusted_score: int
    shadow_adjusted_decision: str
    would_have_changed_decision: bool
    was_shadow_better: bool | None
    simulation_note: str


class AlphaTrackingRecord(BaseModel, frozen=True):
    """Full simulation record for one closed trade."""

    model_config = ConfigDict(frozen=True)

    signal_id: str
    asset: str
    original_score: int
    original_decision: str
    outcome_pnl_pct: float | None  # dimensionless ratio — float OK
    simulation_results: dict[str, MetricSimulation]
    any_metric_would_have_changed_decision: bool
    shadow_adjusted_decisions: dict[str, str]
    created_at: datetime


class PromotionReadinessSummary(BaseModel, frozen=True):
    """Aggregated readiness report for a single metric."""

    model_config = ConfigDict(frozen=True)

    metric_name: str
    total_trades_analyzed: int
    decision_change_count: int
    better_outcomes: int
    worse_outcomes: int
    ready_for_shap_analysis: bool  # total_trades >= 200


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS shadow_alpha_tracking (
    signal_id          TEXT PRIMARY KEY,
    asset              TEXT NOT NULL,
    original_score     INTEGER,
    original_decision  TEXT,
    outcome_pnl_pct    DOUBLE PRECISION,
    simulation_json    JSONB,
    any_changed        BOOLEAN,
    created_at         TIMESTAMPTZ DEFAULT NOW()
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_alpha_asset
ON shadow_alpha_tracking(asset);
"""

_INSERT_SQL = """
INSERT INTO shadow_alpha_tracking
    (signal_id, asset, original_score, original_decision,
     outcome_pnl_pct, simulation_json, any_changed, created_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
ON CONFLICT (signal_id) DO NOTHING
"""


# ---------------------------------------------------------------------------
# ShadowAlphaTracker
# ---------------------------------------------------------------------------


class ShadowAlphaTracker:
    """Retrospectively simulate shadow-metric influence on decisions.

    NEVER modifies confluence weights or scoring config.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        asyncpg_pool: asyncpg.Pool,
        redis_client: redis.asyncio.Redis,  # type: ignore[type-arg]
    ) -> None:
        self._settings = settings
        self._pool = asyncpg_pool
        self._redis = redis_client

    # ── Schema Bootstrap ─────────────────────────────────────────────

    async def ensure_schema(self) -> None:
        """Create shadow_alpha_tracking table if not exists."""
        async with self._pool.acquire() as conn:
            await asyncio.wait_for(
                conn.execute(_CREATE_TABLE_SQL), timeout=10.0,
            )
            await asyncio.wait_for(
                conn.execute(_CREATE_INDEX_SQL), timeout=10.0,
            )

    # ── Core API ─────────────────────────────────────────────────────

    async def record_trade_outcome(
        self,
        signal_id: str,
        original_signal: SignalOutput,
        original_decision: str,
        shadow_metrics_at_entry: ShadowMetrics,
        outcome: TradeOutcome | None,
    ) -> AlphaTrackingRecord:
        """Simulate all 4 metrics and build an AlphaTrackingRecord."""
        simulations = self._run_all_simulations(
            shadow_metrics_at_entry,
            original_signal,
            original_decision,
            outcome,
        )
        return self._assemble_record(
            signal_id, original_signal, original_decision,
            outcome, simulations,
        )

    # ── Simulation Runner ────────────────────────────────────────────

    def _run_all_simulations(
        self,
        shadow: ShadowMetrics,
        original_signal: SignalOutput,
        original_decision: str,
        outcome: TradeOutcome | None,
    ) -> dict[str, MetricSimulation]:
        """Run simulation for every candidate metric."""
        results: dict[str, MetricSimulation] = {}
        for metric_name in _CANDIDATE_WEIGHTS:
            results[metric_name] = self._simulate_metric(
                metric_name, shadow,
                original_signal, original_decision,
                outcome,
            )
        return results

    # ── Per-Metric Simulation ────────────────────────────────────────

    def _simulate_metric(
        self,
        metric_name: str,
        shadow: ShadowMetrics,
        original_signal: SignalOutput,
        original_decision: str,
        outcome: TradeOutcome | None,
    ) -> MetricSimulation:
        """Apply candidate rules for one metric."""
        candidate_weight = _CANDIDATE_WEIGHTS[metric_name]
        shadow_pts = _compute_candidate_pts(
            metric_name, shadow, original_signal,
        )
        return _build_metric_simulation(
            metric_name, candidate_weight, shadow_pts,
            original_signal, original_decision, outcome,
        )

    # ── Record Assembly ──────────────────────────────────────────────

    def _assemble_record(
        self,
        signal_id: str,
        original_signal: SignalOutput,
        original_decision: str,
        outcome: TradeOutcome | None,
        simulations: dict[str, MetricSimulation],
    ) -> AlphaTrackingRecord:
        """Construct AlphaTrackingRecord from simulations."""
        any_changed = any(
            s.would_have_changed_decision for s in simulations.values()
        )
        adjusted_decisions = {
            k: s.shadow_adjusted_decision for k, s in simulations.items()
        }
        pnl = float(outcome.pnl_pct) if outcome is not None else None

        return AlphaTrackingRecord(
            signal_id=signal_id,
            asset=original_signal.asset,
            original_score=original_signal.score,
            original_decision=original_decision,
            outcome_pnl_pct=pnl,
            simulation_results=simulations,
            any_metric_would_have_changed_decision=any_changed,
            shadow_adjusted_decisions=adjusted_decisions,
            created_at=datetime.now(timezone.utc),
        )

    # ── Persistence ──────────────────────────────────────────────────

    async def store_record(self, record: AlphaTrackingRecord) -> None:
        """Insert record to PostgreSQL. On failure: log ERROR, no raise."""
        try:
            await self._execute_insert(record)
        except Exception as exc:
            logger.error(
                "alpha_tracker_store_failed | signal_id={} | err={}",
                record.signal_id,
                str(exc),
            )

    async def _execute_insert(self, record: AlphaTrackingRecord) -> None:
        """Execute the INSERT via asyncpg."""
        sim_dict = {
            k: v.model_dump(mode="json")
            for k, v in record.simulation_results.items()
        }
        sim_json = msgspec.json.encode(sim_dict).decode("utf-8")

        async with self._pool.acquire() as conn:
            await asyncio.wait_for(
                conn.execute(
                    _INSERT_SQL,
                    record.signal_id,
                    record.asset,
                    record.original_score,
                    record.original_decision,
                    record.outcome_pnl_pct,
                    sim_json,
                    record.any_metric_would_have_changed_decision,
                    record.created_at,
                ),
                timeout=5.0,
            )

    # ── Promotion Readiness ──────────────────────────────────────────

    async def get_promotion_readiness_summary(
        self,
        metric_name: str,
    ) -> PromotionReadinessSummary:
        """Query aggregated readiness for a metric."""
        rows = await self._query_metric_rows(metric_name)
        return _build_readiness_summary(metric_name, rows)

    async def _query_metric_rows(
        self,
        metric_name: str,
    ) -> list[asyncpg.Record]:
        """Fetch rows containing the specified metric in simulation_json."""
        sql = """
            SELECT simulation_json, any_changed
            FROM shadow_alpha_tracking
            WHERE simulation_json ? $1
        """
        async with self._pool.acquire() as conn:
            return await asyncio.wait_for(
                conn.fetch(sql, metric_name), timeout=5.0,
            )


# ---------------------------------------------------------------------------
# Pure helper functions — extracted for 40-line cap
# ---------------------------------------------------------------------------


def _compute_candidate_pts(
    metric_name: str,
    shadow: ShadowMetrics,
    signal: SignalOutput,
) -> int:
    """Dispatch to the appropriate candidate scoring rule."""
    if metric_name == "vwap_deviation":
        return _score_vwap_deviation(shadow, signal)
    if metric_name == "ob_imbalance":
        return _score_ob_imbalance(shadow, signal)
    if metric_name == "sr_proximity":
        return _score_sr_proximity(shadow)
    if metric_name == "atr_normalised_move":
        return _score_atr_move(shadow)
    return 0


def _is_signal_long(signal: SignalOutput) -> bool:
    """Check if the signal decision implies a long direction."""
    return signal.decision.value in ("Strong Buy", "Buy")


def _score_vwap_deviation(shadow: ShadowMetrics, signal: SignalOutput) -> int:
    """VWAP Deviation → candidate_pts (max 8)."""
    dev = abs(shadow.vwap_deviation)
    is_long = _is_signal_long(signal)
    above = shadow.vwap_deviation_direction == "ABOVE"
    aligned = (is_long and above) or (not is_long and not above)

    if dev > 0.02:
        return 8 if aligned else 0
    if dev >= 0.01:
        return 5 if aligned else 0
    return 3  # neutral zone


def _score_ob_imbalance(shadow: ShadowMetrics, signal: SignalOutput) -> int:
    """Order Book Imbalance → candidate_pts (max 7)."""
    if shadow.ob_imbalance is None:
        return 4
    if shadow.ob_potential_spoof_detected:
        return 2
    if not shadow.ob_imbalance_persistent:
        return 2
    return _score_persistent_ob(shadow.ob_imbalance, signal)


def _score_persistent_ob(imbalance: float, signal: SignalOutput) -> int:
    """Score persistent, non-spoof OB imbalance."""
    is_long = _is_signal_long(signal)
    if imbalance > 0.3 and is_long:
        return 7
    if imbalance > 0.3 and not is_long:
        return 0
    if imbalance < -0.3:
        return 0
    return 4


def _score_sr_proximity(shadow: ShadowMetrics) -> int:
    """S/R Proximity → candidate_pts (max 6)."""
    prox = shadow.sr_proximity_atr_normalised
    if prox < 0.3:
        return 6
    if prox <= 0.8:
        return 4
    return 2


def _score_atr_move(shadow: ShadowMetrics) -> int:
    """ATR-Normalised Move → candidate_pts (max 5)."""
    move = shadow.atr_normalised_move
    if move > 1.5:
        return 5
    if move >= 0.8:
        return 4
    return 2


def _build_metric_simulation(
    metric_name: str,
    candidate_weight: int,
    shadow_pts: int,
    original_signal: SignalOutput,
    original_decision: str,
    outcome: TradeOutcome | None,
) -> MetricSimulation:
    """Build MetricSimulation from computed shadow points."""
    adjusted_raw = original_signal.raw_confluence_score + shadow_pts
    adjusted_score = round((adjusted_raw / _TOTAL_DENOMINATOR) * 100)
    adjusted_decision = _determine_decision(adjusted_score)
    changed = adjusted_decision != original_decision

    was_better = _evaluate_shadow_quality(
        original_decision, adjusted_decision, outcome,
    )
    note = _build_simulation_note(
        metric_name, shadow_pts, changed, was_better,
    )

    return MetricSimulation(
        metric_name=metric_name,
        candidate_weight=candidate_weight,
        shadow_pts_awarded=shadow_pts,
        shadow_adjusted_score=adjusted_score,
        shadow_adjusted_decision=adjusted_decision,
        would_have_changed_decision=changed,
        was_shadow_better=was_better,
        simulation_note=note,
    )


def _evaluate_shadow_quality(
    original_decision: str,
    adjusted_decision: str,
    outcome: TradeOutcome | None,
) -> bool | None:
    """Compare original vs adjusted decision against actual outcome."""
    if outcome is None:
        return None

    if original_decision == adjusted_decision:
        return None  # no change, no comparison

    outcome_positive = outcome.pnl_pct >= Decimal("0")
    return _classify_quality(
        original_decision, adjusted_decision, outcome_positive,
    )


def _classify_quality(
    original_decision: str,
    adjusted_decision: str,
    outcome_positive: bool,
) -> bool:
    """Determine if shadow flip was beneficial."""
    orig_tradeable = original_decision in _TRADEABLE_DECISIONS
    adj_tradeable = adjusted_decision in _TRADEABLE_DECISIONS

    # Shadow flipped to tradeable, and outcome was positive → better
    if not orig_tradeable and adj_tradeable and outcome_positive:
        return True
    # Shadow flipped from tradeable to safe, and outcome was negative → better
    if orig_tradeable and not adj_tradeable and not outcome_positive:
        return True
    return False


def _build_simulation_note(
    metric_name: str,
    shadow_pts: int,
    changed: bool,
    was_better: bool | None,
) -> str:
    """Build human-readable simulation note."""
    change_str = "CHANGED" if changed else "UNCHANGED"
    quality_str = "BETTER" if was_better is True else (
        "WORSE" if was_better is False else "UNDETERMINED"
    )
    return "{}: +{} pts → {} (outcome: {})".format(
        metric_name, shadow_pts, change_str, quality_str,
    )


def _build_readiness_summary(
    metric_name: str,
    rows: list[asyncpg.Record],
) -> PromotionReadinessSummary:
    """Build PromotionReadinessSummary from query results."""
    total = len(rows)
    change_count = 0
    better = 0
    worse = 0

    for row in rows:
        sim_json = row["simulation_json"]
        change_count, better, worse = _accumulate_row_stats(
            sim_json, metric_name, change_count, better, worse,
        )

    return PromotionReadinessSummary(
        metric_name=metric_name,
        total_trades_analyzed=total,
        decision_change_count=change_count,
        better_outcomes=better,
        worse_outcomes=worse,
        ready_for_shap_analysis=total >= 200,
    )


def _accumulate_row_stats(
    sim_json: dict | str,
    metric_name: str,
    change_count: int,
    better: int,
    worse: int,
) -> tuple[int, int, int]:
    """Extract per-metric stats from a single row's simulation_json."""
    if isinstance(sim_json, str):
        data = msgspec.json.decode(sim_json.encode())
    else:
        data = sim_json

    metric_data = data.get(metric_name, {})
    if metric_data.get("would_have_changed_decision", False):
        change_count += 1
    was_better = metric_data.get("was_shadow_better")
    if was_better is True:
        better += 1
    elif was_better is False:
        worse += 1

    return change_count, better, worse
