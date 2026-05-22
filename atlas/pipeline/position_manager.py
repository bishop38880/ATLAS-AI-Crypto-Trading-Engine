"""Position Sizing Recommender — S2-P3.

Computes anti-martingale position sizing RECOMMENDATIONS that ATLAS
includes in emitted signals. PROMETHEUS receives recommendations via
``polaris:signals:{asset}`` and decides whether to act.

ATLAS never holds exchange credentials, never places orders, never
writes position state. This module is strictly READ-ONLY with respect
to Redis keys owned by PROMETHEUS.

Redis keys consumed (read-only):
    portfolio:value               — Decimal-as-string portfolio value
    positions:open                — msgspec list[str] of open symbols
    position:{asset}:state        — msgspec PositionState
    position:{asset}:scale_in_count — integer count

Architectural note:
    ``PositionManager`` is preserved as a class alias to avoid
    breaking any import that references the original name.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, ConfigDict

from atlas.shared.config import PolarisSettings

# ---------------------------------------------------------------------------
# Decimal constants — NO magic numbers, NO float
# ---------------------------------------------------------------------------

BASE_SIZE_STRONG: Decimal = Decimal("0.050")
"""5.0 % of portfolio — STRONG signal (score >= 82)."""

BASE_SIZE_MODERATE: Decimal = Decimal("0.035")
"""3.5 % of portfolio — MODERATE signal (score 68–81)."""

BASE_SIZE_WEAK: Decimal = Decimal("0.020")
"""2.0 % of portfolio — WEAK signal (score 55–67)."""

CASCADE_PROBE_MULT: Decimal = Decimal("0.25")
"""Stage-1 probe sizing multiplier."""

MAX_SCALE_IN_ADDS: int = 2
"""Maximum number of scale-in additions per position."""

SCALE_IN_THRESHOLD: Decimal = Decimal("0.005")
"""+0.5 % unrealised PnL required to qualify for an add."""

NEVER_ADD_BELOW: Decimal = Decimal("0.0")
"""Zero tolerance for adding to losers."""

MAX_SINGLE_POSITION_PCT: Decimal = Decimal("0.10")
"""10 % portfolio cap per single position."""

# ---------------------------------------------------------------------------
# ATLAS-side mirror of PROMETHEUS PositionState (read-only)
# ---------------------------------------------------------------------------


class PositionState(msgspec.Struct, frozen=True):
    """Mirrors PROMETHEUS PositionState schema — read-only on ATLAS side.

    Changes require a coordinated update across ATLAS and PROMETHEUS.
    """

    asset: str
    position_size_usd: Decimal
    entry_price: Decimal
    side: str
    opened_at: str


# ---------------------------------------------------------------------------
# Pydantic response models — frozen, Decimal-native
# ---------------------------------------------------------------------------


class PositionSizeRecommendation(BaseModel):
    """Entry-sizing recommendation emitted by ATLAS."""

    model_config = ConfigDict(frozen=True)

    asset: str
    recommended_usd: Decimal
    recommended_pct_portfolio: Decimal
    base_size_pct: Decimal
    is_cascade_probe: bool
    status: Literal["APPROVED", "REJECTED"]
    rejection_reason: str | None = None
    signal_score: int
    cycle_timestamp: datetime


class ScaleInRecommendation(BaseModel):
    """Scale-in recommendation emitted by ATLAS."""

    model_config = ConfigDict(frozen=True)

    asset: str
    recommended_add_usd: Decimal
    current_position_usd: Decimal
    unrealized_pnl_pct: Decimal
    scale_in_count_after: int
    status: Literal["APPROVED", "REJECTED"]
    rejection_reason: str | None = None
    cycle_timestamp: datetime


# ---------------------------------------------------------------------------
# Core recommender
# ---------------------------------------------------------------------------


class PositionSizingRecommender:
    """Anti-martingale position-sizing recommender (ATLAS side).

    Reads PROMETHEUS-published state via Redis but **never writes**
    portfolio:value, positions:open, or position:* keys.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
    ) -> None:
        self._settings = settings
        self._redis = redis_client

    # -- public API --------------------------------------------------------

    async def calculate_entry_size(
        self,
        signal: "SignalOutput",
        asset: str,
        is_cascade_stage1: bool = False,
    ) -> PositionSizeRecommendation:
        """Compute entry-size recommendation for a new position."""
        cycle_ts = datetime.now(timezone.utc)
        base_size = _base_size_for_score(signal.score)

        if is_cascade_stage1:
            base_size = base_size * CASCADE_PROBE_MULT

        portfolio_value = await self._get_portfolio_value()
        if portfolio_value is None or portfolio_value <= Decimal("0"):
            return _reject_entry(
                asset, base_size, is_cascade_stage1,
                signal.score, cycle_ts, "PORTFOLIO_UNKNOWN",
            )

        slot_result = await self._check_slots(
            asset, base_size, is_cascade_stage1,
            signal.score, cycle_ts,
        )
        if slot_result is not None:
            return slot_result

        return _compute_approved_entry(
            asset, base_size, portfolio_value,
            is_cascade_stage1, signal.score, cycle_ts,
        )

    async def calculate_scale_in(
        self,
        asset: str,
        current_unrealized_pnl_pct: Decimal,
    ) -> ScaleInRecommendation:
        """Compute scale-in recommendation for an existing position."""
        cycle_ts = datetime.now(timezone.utc)

        result = await self._validate_scale_in_inputs(
            asset, current_unrealized_pnl_pct, cycle_ts,
        )
        if isinstance(result, ScaleInRecommendation):
            return result

        # result is the validated PositionState — single Redis read
        pos_state: PositionState = result

        count = await self._read_scale_in_count(asset)
        if count >= MAX_SCALE_IN_ADDS:
            return _reject_scale_in(
                asset, pos_state.position_size_usd,
                current_unrealized_pnl_pct, count, cycle_ts,
                "MAX_ADDS_REACHED",
            )

        add_size = pos_state.position_size_usd * Decimal("0.5")

        return ScaleInRecommendation(
            asset=asset,
            recommended_add_usd=add_size,
            current_position_usd=pos_state.position_size_usd,
            unrealized_pnl_pct=current_unrealized_pnl_pct,
            scale_in_count_after=count + 1,
            status="APPROVED",
            rejection_reason=None,
            cycle_timestamp=cycle_ts,
        )

    async def _validate_scale_in_inputs(
        self,
        asset: str,
        pnl_pct: Decimal,
        cycle_ts: datetime,
    ) -> ScaleInRecommendation | PositionState:
        """Return rejection or validated PositionState (single Redis read)."""
        if _is_invalid_decimal(pnl_pct):
            logger.error("invalid pnl input | asset={}", asset)
            return _reject_scale_in(
                asset, Decimal("0"), pnl_pct,
                0, cycle_ts, "INVALID_INPUT",
            )

        pos_state = await self._read_position_state(asset)
        if pos_state is None:
            return _reject_scale_in(
                asset, Decimal("0"), pnl_pct,
                0, cycle_ts, "NO_OPEN_POSITION",
            )

        if pnl_pct <= NEVER_ADD_BELOW:
            logger.info(
                "scale-in rejected | asset={} | reason={} | pnl={}",
                asset, "losing", str(pnl_pct),
            )
            return _reject_scale_in(
                asset, pos_state.position_size_usd,
                pnl_pct, 0, cycle_ts, "POSITION_LOSING",
            )

        if pnl_pct < SCALE_IN_THRESHOLD:
            return _reject_scale_in(
                asset, pos_state.position_size_usd,
                pnl_pct, 0, cycle_ts, "BELOW_THRESHOLD",
            )

        return pos_state

    # -- private Redis readers ---------------------------------------------

    async def _get_portfolio_value(self) -> Decimal | None:
        """Read ``portfolio:value`` from Redis (PROMETHEUS-owned key)."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get("portfolio:value"), timeout=5.0,
            )
            if raw is None:
                return None
            return Decimal(raw.decode() if isinstance(raw, bytes) else raw)
        except (InvalidOperation, Exception) as exc:
            logger.warning(
                "portfolio value degraded | op={} | err={}",
                "_get_portfolio_value",
                str(exc),
            )
            return None

    async def _read_position_state(self, asset: str) -> PositionState | None:
        """Read ``position:{asset}:state`` from Redis."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get(f"position:{asset}:state"),
                timeout=5.0,
            )
            if raw is None:
                return None
            return msgspec.json.decode(raw, type=PositionState)
        except Exception as exc:
            logger.warning(
                "position state degraded | op={} | asset={} | err={}",
                "_read_position_state",
                asset,
                str(exc),
            )
            return None

    async def _read_scale_in_count(self, asset: str) -> int:
        """Read ``position:{asset}:scale_in_count`` from Redis."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get(f"position:{asset}:scale_in_count"),
                timeout=5.0,
            )
            if raw is None:
                return 0
            return int(raw)
        except Exception as exc:
            logger.warning(
                "scale-in count degraded | op={} | asset={} | err={}",
                "_read_scale_in_count",
                asset,
                str(exc),
            )
            return 0

    async def _check_slots(
        self,
        asset: str,
        base_size: Decimal,
        is_cascade: bool,
        score: int,
        cycle_ts: datetime,
    ) -> PositionSizeRecommendation | None:
        """Return REJECTED recommendation if slots full, else None."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get("positions:open"), timeout=5.0,
            )
            if raw is None:
                return None
            open_list: list[str] = msgspec.json.decode(raw, type=list[str])
            if len(open_list) >= 6:
                return _reject_entry(
                    asset, base_size, is_cascade,
                    score, cycle_ts, "SLOTS_FULL",
                )
        except Exception as exc:
            logger.warning(
                "positions:open degraded | op={} | err={}",
                "_check_slots",
                str(exc),
            )
        return None


# ---------------------------------------------------------------------------
# Pure helpers (no self, no I/O)
# ---------------------------------------------------------------------------


def _compute_approved_entry(
    asset: str,
    base_size: Decimal,
    portfolio_value: Decimal,
    is_cascade: bool,
    score: int,
    cycle_ts: datetime,
) -> PositionSizeRecommendation:
    """Build an APPROVED entry recommendation with cap enforcement."""
    usd_size = base_size * portfolio_value
    cap = portfolio_value * MAX_SINGLE_POSITION_PCT
    usd_size = min(usd_size, cap)
    return PositionSizeRecommendation(
        asset=asset,
        recommended_usd=usd_size,
        recommended_pct_portfolio=base_size,
        base_size_pct=base_size,
        is_cascade_probe=is_cascade,
        status="APPROVED",
        rejection_reason=None,
        signal_score=score,
        cycle_timestamp=cycle_ts,
    )


def _base_size_for_score(score: int) -> Decimal:
    """Map confluence score to base position size percentage."""
    if score >= 82:
        return BASE_SIZE_STRONG
    if score >= 68:
        return BASE_SIZE_MODERATE
    return BASE_SIZE_WEAK


def _is_invalid_decimal(value: Decimal) -> bool:
    """Return True if Decimal is NaN or infinite."""
    return value.is_nan() or value.is_infinite()


def _reject_entry(
    asset: str,
    base_size: Decimal,
    is_cascade: bool,
    score: int,
    cycle_ts: datetime,
    reason: str,
) -> PositionSizeRecommendation:
    """Build a REJECTED entry recommendation."""
    return PositionSizeRecommendation(
        asset=asset,
        recommended_usd=Decimal("0"),
        recommended_pct_portfolio=Decimal("0"),
        base_size_pct=base_size,
        is_cascade_probe=is_cascade,
        status="REJECTED",
        rejection_reason=reason,
        signal_score=score,
        cycle_timestamp=cycle_ts,
    )


def _reject_scale_in(
    asset: str,
    current_pos_usd: Decimal,
    pnl_pct: Decimal,
    count: int,
    cycle_ts: datetime,
    reason: str,
) -> ScaleInRecommendation:
    """Build a REJECTED scale-in recommendation."""
    return ScaleInRecommendation(
        asset=asset,
        recommended_add_usd=Decimal("0"),
        current_position_usd=current_pos_usd,
        unrealized_pnl_pct=pnl_pct,
        scale_in_count_after=count,
        status="REJECTED",
        rejection_reason=reason,
        cycle_timestamp=cycle_ts,
    )


# ---------------------------------------------------------------------------
# Backward-compat alias
# ---------------------------------------------------------------------------

PositionManager = PositionSizingRecommender
"""Legacy alias — prefer ``PositionSizingRecommender``."""

# ---------------------------------------------------------------------------
# Deferred import to avoid circular dependency with signal.py
# ---------------------------------------------------------------------------

from atlas.models.signal import SignalOutput as SignalOutput  # noqa: E402, F811
