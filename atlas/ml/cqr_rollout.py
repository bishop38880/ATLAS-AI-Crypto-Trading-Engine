# SKIP_INVARIANT_CHECK
"""Graduated CQR rollout — stage logic, metrics, and promotion checks.

Implements the SHADOW → ACTIVE_TIGHT_ONLY → FULL rollout pipeline.
CQR bounds never modify the point estimate. The ``use_for_leverage``
flag tells PROMETHEUS whether to consume bounds for position sizing.

Invariants:
    - Default stage is SHADOW (log-only).
    - Promotion requires explicit human approval.
    - Point estimate is never touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from loguru import logger

from atlas.ml.uncertainty_propagator import UncertaintyBounds


# ---------------------------------------------------------------------------
# Stage enum
# ---------------------------------------------------------------------------


class CQRRolloutStage(str, Enum):
    """Three-stage graduated rollout for CQR bounds."""

    SHADOW = "SHADOW"
    ACTIVE_TIGHT_ONLY = "ACTIVE_TIGHT_ONLY"
    FULL = "FULL"


# ---------------------------------------------------------------------------
# Stage application result
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    """Result of applying rollout stage logic to bounds."""

    bounds: UncertaintyBounds
    use_for_leverage: bool
    stage: CQRRolloutStage


# ---------------------------------------------------------------------------
# Metrics dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CQRRolloutMetrics:
    """Snapshot of rollout performance metrics."""

    timestamp: datetime
    stage: str
    avg_width: float
    num_trades_using_bounds: int
    sharpe_bounds: float | None
    sharpe_point: float | None


_TIGHT_WIDTH_THRESHOLD = 20


# ---------------------------------------------------------------------------
# Pure stage logic
# ---------------------------------------------------------------------------


def apply_rollout_stage(
    stage: CQRRolloutStage,
    bounds: UncertaintyBounds,
    raw_score: int,
) -> RolloutDecision:
    """Determine whether bounds should influence leverage decisions.

    Args:
        stage: Current rollout stage.
        bounds: Computed uncertainty bounds.
        raw_score: Raw confluence score (unchanged).

    Returns:
        RolloutDecision with use_for_leverage flag.
    """
    if stage == CQRRolloutStage.SHADOW:
        return _shadow_decision(bounds, stage)
    if stage == CQRRolloutStage.ACTIVE_TIGHT_ONLY:
        return _tight_only_decision(bounds, stage)
    return RolloutDecision(
        bounds=bounds, use_for_leverage=True, stage=stage,
    )


def _shadow_decision(
    bounds: UncertaintyBounds, stage: CQRRolloutStage,
) -> RolloutDecision:
    """SHADOW: compute and log bounds, never apply to leverage."""
    logger.info(
        "cqr_shadow | width={} | lower={} | upper={}",
        bounds.width, bounds.lower, bounds.upper,
    )
    return RolloutDecision(
        bounds=bounds, use_for_leverage=False, stage=stage,
    )


def _tight_only_decision(
    bounds: UncertaintyBounds, stage: CQRRolloutStage,
) -> RolloutDecision:
    """ACTIVE_TIGHT_ONLY: apply bounds only when width < 20."""
    use = bounds.width < _TIGHT_WIDTH_THRESHOLD
    logger.info(
        "cqr_tight_only | width={} | apply={}",
        bounds.width, use,
    )
    return RolloutDecision(
        bounds=bounds, use_for_leverage=use, stage=stage,
    )


# ---------------------------------------------------------------------------
# Promotion criteria checks (pure functions)
# ---------------------------------------------------------------------------


def check_shadow_promotion(
    avg_width_current: float,
    avg_width_initial: float,
) -> bool:
    """Check if SHADOW → ACTIVE_TIGHT_ONLY promotion criteria are met.

    Requires average bounds width to have decreased by ≥30%.

    Returns:
        True if promotion criteria met (human approval still required).
    """
    if avg_width_initial <= 0:
        return False
    decrease_pct = (
        (avg_width_initial - avg_width_current) / avg_width_initial
    )
    return decrease_pct >= 0.30


def check_tight_promotion(
    num_trades: int,
    sharpe_bounds: float,
    sharpe_point: float,
) -> bool:
    """Check if ACTIVE_TIGHT_ONLY → FULL promotion criteria are met.

    Requires ≥50 trades and Sharpe using tight bounds not worse
    than point-estimate Sharpe.

    Returns:
        True if promotion criteria met (human approval still required).
    """
    if num_trades < 50:
        return False
    return sharpe_bounds >= sharpe_point


# ---------------------------------------------------------------------------
# Metrics persistence
# ---------------------------------------------------------------------------


async def log_rollout_metrics(
    pool: Any,
    metrics: CQRRolloutMetrics,
) -> None:
    """INSERT rollout metrics into cqr_rollout_metrics table.

    Args:
        pool: asyncpg connection pool.
        metrics: Snapshot to persist.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cqr_rollout_metrics
                    (timestamp, stage, avg_width,
                     num_trades_using_bounds, sharpe_bounds, sharpe_point)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                metrics.timestamp,
                metrics.stage,
                metrics.avg_width,
                metrics.num_trades_using_bounds,
                metrics.sharpe_bounds,
                metrics.sharpe_point,
                timeout=10.0,
            )
    except Exception as exc:
        logger.error("cqr_metrics_persist_failed | exc={}", exc)


# ---------------------------------------------------------------------------
# Promotion suggestion (does NOT auto-promote)
# ---------------------------------------------------------------------------


async def suggest_promotion(
    pool: Any,
    current_stage: CQRRolloutStage,
    avg_width_current: float,
    avg_width_initial: float,
    num_trades: int,
    sharpe_bounds: float,
    sharpe_point: float,
) -> str | None:
    """Check promotion criteria and log suggestion if met.

    Does NOT change the stage. Returns suggested next stage or None.
    Human must approve via Incident Console and update config.

    Args:
        pool: asyncpg connection pool.
        current_stage: Current rollout stage.
        avg_width_current: Current average bounds width.
        avg_width_initial: Initial calibration average width.
        num_trades: Number of trades executed using bounds.
        sharpe_bounds: Sharpe ratio using bounds.
        sharpe_point: Sharpe ratio using point estimate.

    Returns:
        Suggested next stage string, or None if no promotion.
    """
    suggestion = _evaluate_promotion(
        current_stage, avg_width_current, avg_width_initial,
        num_trades, sharpe_bounds, sharpe_point,
    )
    if suggestion:
        await _persist_suggestion(pool, current_stage, suggestion)
    return suggestion


def _evaluate_promotion(
    current_stage: CQRRolloutStage,
    avg_width_current: float,
    avg_width_initial: float,
    num_trades: int,
    sharpe_bounds: float,
    sharpe_point: float,
) -> str | None:
    """Pure function to evaluate promotion criteria."""
    if current_stage == CQRRolloutStage.SHADOW:
        if check_shadow_promotion(avg_width_current, avg_width_initial):
            return CQRRolloutStage.ACTIVE_TIGHT_ONLY.value
    elif current_stage == CQRRolloutStage.ACTIVE_TIGHT_ONLY:
        if check_tight_promotion(num_trades, sharpe_bounds, sharpe_point):
            return CQRRolloutStage.FULL.value
    return None


async def _persist_suggestion(
    pool: Any,
    current_stage: CQRRolloutStage,
    suggested_stage: str,
) -> None:
    """Log promotion suggestion for human review."""
    logger.warning(
        "cqr_promotion_suggested | from={} | to={} | "
        "REQUIRES_HUMAN_APPROVAL",
        current_stage.value, suggested_stage,
    )
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO cqr_rollout_metrics
                    (timestamp, stage, avg_width,
                     num_trades_using_bounds, sharpe_bounds, sharpe_point)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                datetime.now(timezone.utc),
                f"PROMOTION_SUGGESTED:{suggested_stage}",
                0.0, 0, None, None,
                timeout=10.0,
            )
    except Exception as exc:
        logger.error("cqr_promotion_log_failed | exc={}", exc)
