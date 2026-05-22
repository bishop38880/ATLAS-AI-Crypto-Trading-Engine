"""Signal extraction from Coinbase premium rolling windows."""

from __future__ import annotations

import time
from typing import Any

import msgspec
from loguru import logger
from redis.asyncio import Redis

STRONG_POSITIVE = 0.15
MILD_POSITIVE = 0.05
MILD_NEGATIVE = -0.05
STRONG_NEGATIVE = -0.15

WINDOWS: dict[str, int] = {
    "1min": 60,
    "5min": 300,
    "15min": 900,
    "1hr": 3600,
}

REDIS_PREFIX = "premium"
ETH_PREMIUM_WEIGHT = 0.6


def calculate_premium_pct(coinbase_price: float, binance_price: float) -> float | None:
    """Compute premium % = (CB − BN) / BN × 100. Returns None if BN is zero."""
    if binance_price == 0:
        return None
    return (coinbase_price - binance_price) / binance_price * 100.0


def compute_signals(
    history: list[tuple[float, float]],
    current_premium: float,
) -> dict[str, Any]:
    """Compute derived signals from premium history for confluence scoring."""
    now = time.time()
    signals: dict[str, Any] = {
        "current_premium_pct": round(current_premium, 4),
        "signal": _classify(current_premium),
    }

    for window_name, window_secs in WINDOWS.items():
        cutoff = now - window_secs
        window_vals = [premium for ts, premium in history if ts >= cutoff]
        if window_vals:
            avg = sum(window_vals) / len(window_vals)
            signals[f"avg_{window_name}"] = round(avg, 4)
            signals[f"count_{window_name}"] = len(window_vals)
        else:
            signals[f"avg_{window_name}"] = None
            signals[f"count_{window_name}"] = 0

    avg_15m = signals.get("avg_15min")
    avg_1hr = signals.get("avg_1hr")
    if avg_15m is not None and avg_1hr is not None:
        trend_delta = avg_15m - avg_1hr
        if trend_delta > 0.03:
            signals["trend"] = "rising"
        elif trend_delta < -0.03:
            signals["trend"] = "falling"
        else:
            signals["trend"] = "flat"
    else:
        signals["trend"] = "insufficient_data"

    one_hr_vals = [premium for ts, premium in history if ts >= now - 3600]
    if len(one_hr_vals) >= 10:
        mean = sum(one_hr_vals) / len(one_hr_vals)
        variance = sum((value - mean) ** 2 for value in one_hr_vals) / len(one_hr_vals)
        std = variance**0.5
        signals["zscore_1hr"] = (
            round((current_premium - mean) / std, 2) if std > 0 else 0.0
        )
        signals["mean_1hr"] = round(mean, 4)
        signals["std_1hr"] = round(std, 4)
    else:
        signals["zscore_1hr"] = None
        signals["mean_1hr"] = None
        signals["std_1hr"] = None

    signals["scoring_contribution"] = calculate_score_contribution(
        current_premium,
        signals.get("zscore_1hr"),
    )
    signals["computed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return signals


def _classify(premium: float) -> str:
    if premium >= STRONG_POSITIVE:
        return "strong_us_buying"
    if premium >= MILD_POSITIVE:
        return "mild_us_buying"
    if premium <= STRONG_NEGATIVE:
        return "offshore_driven_fragile"
    if premium <= MILD_NEGATIVE:
        return "us_absent_or_selling"
    return "neutral"


def calculate_score_contribution(
    premium: float,
    zscore: float | None,
) -> float:
    """Map premium to ±8 scoring points within the derivatives bucket."""
    if premium >= STRONG_POSITIVE:
        base = 8.0
    elif premium >= MILD_POSITIVE:
        span = STRONG_POSITIVE - MILD_POSITIVE
        base = 4.0 + (premium - MILD_POSITIVE) / span * 4.0
    elif premium <= STRONG_NEGATIVE:
        base = -8.0
    elif premium <= MILD_NEGATIVE:
        span = STRONG_NEGATIVE - MILD_NEGATIVE
        base = -4.0 + (premium - MILD_NEGATIVE) / span * (-4.0)
    else:
        base = (premium / MILD_POSITIVE) * 4.0 if MILD_POSITIVE != 0 else 0.0

    if zscore is not None and abs(zscore) > 2.0:
        amplifier = min(1.2, 1.0 + (abs(zscore) - 2.0) * 0.1)
        base = base * amplifier

    return round(max(-8.0, min(8.0, base)), 2)


async def fetch_premium_scoring_inputs(
    redis: Redis,
    symbol: str,
) -> tuple[float, str]:
    """
    Read cached premium signals from Redis for confluence scoring.

    Returns (contribution_pts, signal_label). Graceful zero when missing.
    ETH contribution is weighted at 60% of BTC per product notes.
    """
    sym = symbol.upper()
    if sym not in ("BTC", "ETH"):
        return 0.0, "n/a"

    raw = await redis.get(f"{REDIS_PREFIX}:{sym.lower()}:signals")
    if raw is None:
        return 0.0, "n/a"

    try:
        if isinstance(raw, bytes):
            payload = msgspec.json.decode(raw)
        else:
            payload = msgspec.json.decode(str(raw).encode())
    except Exception as exc:
        logger.warning(
            "premium_signals_decode_failed | symbol={} | err={}",
            sym,
            str(exc),
        )
        return 0.0, "n/a"

    contribution = float(payload.get("scoring_contribution", 0.0))
    signal_label = str(payload.get("signal", "neutral"))
    if sym == "ETH":
        contribution = round(contribution * ETH_PREMIUM_WEIGHT, 2)
    return contribution, signal_label
