"""
Multi-timeframe confluence scoring engine.
Computes weighted average + momentum alignment multiplier.
All arithmetic uses Decimal — no float.
Staleness is checked before scoring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

from loguru import logger

from atlas.models.mtf import AlignmentLabel, MTFBlock, TimeframeLabel

WEIGHT_4H = Decimal("0.50")
WEIGHT_30M = Decimal("0.30")
WEIGHT_15M = Decimal("0.20")

MULTIPLIER_BUILDING = Decimal("1.10")
MULTIPLIER_FADING = Decimal("0.88")
MULTIPLIER_FLAT = Decimal("1.00")
MULTIPLIER_RECOVERING = Decimal("1.00")

STALE_4H = 480
STALE_30M = 60
STALE_15M = 30
FLAT_THRESHOLD = 15


def _minutes_between(start: datetime, end: datetime) -> Decimal:
    delta = end - start
    total_seconds = (
        Decimal(delta.days) * Decimal("86400")
        + Decimal(delta.seconds)
        + Decimal(delta.microseconds) / Decimal("1000000")
    )
    return total_seconds / Decimal("60")


def _check_staleness(
    ts_iso: str,
    stale_min: int,
    tf: TimeframeLabel,
    now: datetime,
) -> bool:
    """Returns True if the timeframe packet is too old to use."""
    try:
        ts = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_minutes = _minutes_between(ts, now)
        if age_minutes > Decimal(stale_min):
            logger.warning(
                "stale_tf_detected | tf={} age_min={} limit={}",
                tf,
                age_minutes,
                stale_min,
            )
            return True
        return False
    except ValueError:
        logger.error("invalid_timestamp | tf={} ts={}", tf, ts_iso)
        return True


def _first_stale_timeframe(
    ts_4h: str,
    ts_30m: str,
    ts_15m: str,
    now: datetime,
) -> TimeframeLabel | None:
    if _check_staleness(ts_4h, STALE_4H, "4h", now):
        return "4h"
    if _check_staleness(ts_30m, STALE_30M, "30m", now):
        return "30m"
    if _check_staleness(ts_15m, STALE_15M, "15m", now):
        return "15m"
    return None


def _detect_alignment(sc_4h: int, sc_30m: int, sc_15m: int) -> AlignmentLabel:
    """
    Classify the momentum direction across timeframes.
    BUILDING:   15m > 30m > 4h  — signal strengthening into entry (best)
    FADING:     4h > 30m > 15m  — signal weakening into entry (worst)
    FLAT:       all three within FLAT_THRESHOLD — no clear direction
    RECOVERING: partial building (15m > 4h but 30m lags) — ambiguous
    """
    hi = max(sc_4h, sc_30m, sc_15m)
    lo = min(sc_4h, sc_30m, sc_15m)
    spread_dec = Decimal(hi - lo)
    if spread_dec <= Decimal(FLAT_THRESHOLD):
        return "FLAT"
    if sc_15m > sc_30m > sc_4h:
        return "BUILDING"
    if sc_4h > sc_30m > sc_15m:
        return "FADING"
    if sc_15m > sc_4h and sc_30m < sc_15m and sc_30m < sc_4h:
        return "RECOVERING"
    return "FLAT"


def _multiplier_for(alignment: AlignmentLabel) -> Decimal:
    match alignment:
        case "BUILDING":
            return MULTIPLIER_BUILDING
        case "FADING":
            return MULTIPLIER_FADING
        case "FLAT":
            return MULTIPLIER_FLAT
        case "RECOVERING":
            return MULTIPLIER_RECOVERING


def _weighted_base_avg(sc_4h: int, sc_30m: int, sc_15m: int) -> Decimal:
    return (
        Decimal(sc_4h) * WEIGHT_4H
        + Decimal(sc_30m) * WEIGHT_30M
        + Decimal(sc_15m) * WEIGHT_15M
    )


_CANON_WEIGHTS = (WEIGHT_4H, WEIGHT_30M, WEIGHT_15M)


def _stale_placeholder_block(
    sc_4h: int,
    sc_30m: int,
    sc_15m: int,
    stale_tf: TimeframeLabel,
) -> MTFBlock:
    """Return sentinel block — avg forced to zero for downstream NO_TRADE logic."""
    return MTFBlock(
        sc_4h=sc_4h,
        sc_30m=sc_30m,
        sc_15m=sc_15m,
        weights=_CANON_WEIGHTS,
        base_avg=0,
        alignment="FLAT",
        multiplier=Decimal("1.00"),
        avg=0,
        stale_tf=stale_tf,
        is_stale=True,
    )


def _finalize_live_block(
    sc_4h: int,
    sc_30m: int,
    sc_15m: int,
) -> MTFBlock:
    base_avg_dec = _weighted_base_avg(sc_4h, sc_30m, sc_15m).quantize(
        Decimal("1"),
        rounding=ROUND_HALF_UP,
    )
    alignment = _detect_alignment(sc_4h, sc_30m, sc_15m)
    mult = _multiplier_for(alignment)
    adjusted = (base_avg_dec * mult).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    avg_cap = min(int(adjusted), 220)
    logger.info(
        "mtf_computed | 4h={} 30m={} 15m={} base={} alignment={} mult={} avg={}",
        sc_4h,
        sc_30m,
        sc_15m,
        int(base_avg_dec),
        alignment,
        mult,
        avg_cap,
    )
    return MTFBlock(
        sc_4h=sc_4h,
        sc_30m=sc_30m,
        sc_15m=sc_15m,
        weights=_CANON_WEIGHTS,
        base_avg=int(base_avg_dec),
        alignment=alignment,
        multiplier=mult,
        avg=avg_cap,
        stale_tf=None,
        is_stale=False,
    )


def compute_mtf_block(
    sc_4h: int,
    sc_30m: int,
    sc_15m: int,
    ts_4h: str,
    ts_30m: str,
    ts_15m: str,
) -> MTFBlock:
    """Staleness first; zero avg when stale, else Decimal-weighted fusion."""
    now = datetime.now(timezone.utc)
    stale_tf = _first_stale_timeframe(ts_4h, ts_30m, ts_15m, now)
    if stale_tf is not None:
        return _stale_placeholder_block(sc_4h, sc_30m, sc_15m, stale_tf)
    return _finalize_live_block(sc_4h, sc_30m, sc_15m)


def mtf_to_decision_label(avg: int, alignment: AlignmentLabel) -> str:
    """
    Map the adjusted avg to a trade decision label.
    FADING setups near threshold are downgraded one level.
    """
    if avg >= 170:
        if alignment == "FADING":
            return "BUY"
        return "STRONG"
    if avg >= 150:
        if alignment == "FADING":
            return "WEAK"
        return "BUY"
    if avg >= 130:
        return "WEAK"
    return "NO_TRADE"
