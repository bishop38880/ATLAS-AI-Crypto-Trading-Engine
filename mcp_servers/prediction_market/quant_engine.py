"""
Quantitative engine for prediction market probability extraction.

Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
Computes BBO midpoints, evaluates bid-ask spreads, and calculates the
``capital_conviction_score`` — a 0.0–1.0 metric that scales raw
probability by market liquidity, spread tightness, and open interest.

All CPU-intensive calculations are offloaded via ``asyncio.to_thread``
to prevent blocking the MCP event loop.

Sentinel Invariants:
  - asyncio.to_thread for CPU-bound math
  - Decimal for financial values (volume, OI)
  - float for dimensionless probabilities/scores
  - Max 40 lines per function
  - Loguru positional format
"""

from __future__ import annotations

import asyncio
import math
import re
from decimal import Decimal
from typing import Any

from loguru import logger

from .models import (
    MarketStatus,
    OrderBookSnapshot,
    Platform,
    UnifiedEventProbability,
)


# ──────────────────────────────────────────────────────────────
# Configuration constants
# ──────────────────────────────────────────────────────────────

_SPREAD_PENALTY_THRESHOLD: float = 0.10
_ILLIQUID_SPREAD_THRESHOLD: float = 0.25
_OI_FLOOR_USD: float = 1000.0
_OI_CEILING_USD: float = 10_000_000.0
_VOLUME_CEILING_USD: float = 50_000_000.0


# ──────────────────────────────────────────────────────────────
# BBO Midpoint Extraction (sync — runs in thread)
# ──────────────────────────────────────────────────────────────

def _calculate_bbo_midpoint(
    bids: list[list[float]],
    asks: list[list[float]],
) -> tuple[float, float, float, float]:
    """
    Extract BBO midpoint from order book levels.

    Returns:
        Tuple of (midpoint, best_bid, best_ask, spread).
        Returns (0.5, 0.0, 1.0, 1.0) if both sides are empty.
    """
    best_bid: float = bids[0][0] if bids else 0.0
    best_ask: float = asks[0][0] if asks else 1.0
    spread: float = best_ask - best_bid
    midpoint: float = (best_bid + best_ask) / 2.0
    return midpoint, best_bid, best_ask, spread


def _calculate_book_depth_usd(
    levels: list[list[float]],
    depth_levels: int = 5,
) -> float:
    """
    Sum USD-equivalent size across top N levels.

    Args:
        levels: List of [price, size] levels.
        depth_levels: Number of levels to sum.

    Returns:
        Total USD-equivalent depth.
    """
    total: float = 0.0
    for i, level in enumerate(levels):
        if i >= depth_levels:
            break
        price: float = level[0] if len(level) > 0 else 0.0
        size: float = level[1] if len(level) > 1 else 0.0
        total += price * size
    return total


# ──────────────────────────────────────────────────────────────
# Capital Conviction Score (sync — runs in thread)
# ──────────────────────────────────────────────────────────────

def _calculate_conviction_score(
    probability: float,
    spread: float,
    open_interest_usd: float,
    volume_usd: float,
) -> float:
    """
    Compute capital-weighted conviction score.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.
    A 95% probability on $500 volume is noise. A 65% probability with
    $10M OI is a hard macro signal. This score captures that distinction.

    Formula:
      spread_factor = max(0, 1 - spread / threshold)
      oi_factor = log(clamp(OI)) / log(ceiling)
      conviction = probability × spread_factor × oi_factor

    Args:
        probability: BBO midpoint (0.0–1.0).
        spread: Bid-ask spread.
        open_interest_usd: Total OI in USD.
        volume_usd: Total volume in USD.

    Returns:
        Conviction score clamped to 0.0–1.0.
    """
    spread_factor: float = _calculate_spread_factor(spread)
    oi_factor: float = _calculate_oi_factor(open_interest_usd)
    volume_bonus: float = _calculate_volume_bonus(volume_usd)

    raw_score: float = probability * spread_factor * (oi_factor + volume_bonus)
    return max(0.0, min(1.0, raw_score))


def _calculate_spread_factor(spread: float) -> float:
    """
    Penalise wide spreads — zero conviction above threshold.

    Args:
        spread: Bid-ask spread (0.0–1.0).

    Returns:
        Spread penalty factor (0.0–1.0).
    """
    if spread >= _ILLIQUID_SPREAD_THRESHOLD:
        return 0.0
    if spread <= 0.01:
        return 1.0
    return max(0.0, 1.0 - (spread / _SPREAD_PENALTY_THRESHOLD))


def _calculate_oi_factor(open_interest_usd: float) -> float:
    """
    Log-scale OI contribution.

    Args:
        open_interest_usd: Open interest in USD.

    Returns:
        OI factor (0.0–1.0).
    """
    if open_interest_usd < _OI_FLOOR_USD:
        return 0.0
    clamped: float = min(open_interest_usd, _OI_CEILING_USD)
    log_oi: float = math.log(clamped)
    log_ceiling: float = math.log(_OI_CEILING_USD)
    return log_oi / log_ceiling


def _calculate_volume_bonus(volume_usd: float) -> float:
    """
    Small bonus for high volume (capped at 0.15).

    Args:
        volume_usd: Total traded volume in USD.

    Returns:
        Volume bonus (0.0–0.15).
    """
    if volume_usd <= 0.0:
        return 0.0
    clamped: float = min(volume_usd, _VOLUME_CEILING_USD)
    return 0.15 * (math.log(clamped + 1) / math.log(_VOLUME_CEILING_USD + 1))


# ──────────────────────────────────────────────────────────────
# Market Status Classification (sync — runs in thread)
# ──────────────────────────────────────────────────────────────

def _classify_market_status(
    spread: float,
    open_interest_usd: float,
) -> MarketStatus:
    """Classify market data quality based on spread and OI."""
    if spread >= _ILLIQUID_SPREAD_THRESHOLD or open_interest_usd < _OI_FLOOR_USD:
        return MarketStatus.ILLIQUID
    if spread >= _SPREAD_PENALTY_THRESHOLD:
        return MarketStatus.DEGRADED
    return MarketStatus.OK


# ──────────────────────────────────────────────────────────────
# Async wrappers (offload to thread)
# ──────────────────────────────────────────────────────────────

async def compute_bbo_midpoint(
    order_book: OrderBookSnapshot,
) -> tuple[float, float, float, float]:
    """
    Async wrapper: compute BBO midpoint via asyncio.to_thread.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.

    Args:
        order_book: Raw order book snapshot.

    Returns:
        Tuple of (midpoint, best_bid, best_ask, spread).
    """
    return await asyncio.to_thread(
        _calculate_bbo_midpoint, order_book.bids, order_book.asks
    )


async def compute_conviction_score(
    probability: float,
    spread: float,
    open_interest_usd: float,
    volume_usd: float,
) -> float:
    """
    Async wrapper: compute capital-weighted conviction via asyncio.to_thread.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.

    Args:
        probability: BBO midpoint probability.
        spread: Bid-ask spread.
        open_interest_usd: OI in USD.
        volume_usd: Volume in USD.

    Returns:
        Conviction score (0.0–1.0).
    """
    return await asyncio.to_thread(
        _calculate_conviction_score,
        probability, spread, open_interest_usd, volume_usd,
    )


async def build_unified_event(
    market_data: dict[str, Any],
    order_book: OrderBookSnapshot,
    platform: Platform,
) -> UnifiedEventProbability:
    """
    Build a UnifiedEventProbability from raw market data and order book.

    Section 5 Architecture: Macro Context — Capital-Weighted Probabilities.

    Args:
        market_data: Raw market metadata dict from the platform API.
        order_book: Order book snapshot.
        platform: Source platform enum.

    Returns:
        Fully populated UnifiedEventProbability.
    """
    midpoint, best_bid, best_ask, spread = await compute_bbo_midpoint(
        order_book
    )
    volume_raw: float = _extract_volume(market_data, platform)
    oi_raw: float = _extract_oi(market_data, platform)
    conviction: float = await compute_conviction_score(
        midpoint, spread, oi_raw, volume_raw
    )
    status: MarketStatus = await asyncio.to_thread(
        _classify_market_status, spread, oi_raw
    )
    return _assemble_unified_event(
        market_data, platform, midpoint, best_bid, best_ask,
        spread, volume_raw, oi_raw, conviction, status,
    )


def _assemble_unified_event(
    market_data: dict[str, Any],
    platform: Platform,
    midpoint: float,
    best_bid: float,
    best_ask: float,
    spread: float,
    volume_raw: float,
    oi_raw: float,
    conviction: float,
    status: MarketStatus,
) -> UnifiedEventProbability:
    """Assemble the final UnifiedEventProbability model."""
    return UnifiedEventProbability(
        market_id=_extract_market_id(market_data, platform),
        platform=platform,
        question=_extract_question(market_data, platform),
        category=_infer_category(market_data, platform),
        probability=midpoint,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=spread,
        volume_usd=Decimal(str(round(volume_raw, 2))),
        open_interest_usd=Decimal(str(round(oi_raw, 2))),
        capital_conviction_score=conviction,
        end_date=_extract_end_date(market_data, platform),
        status=status,
    )


# ──────────────────────────────────────────────────────────────
# Platform-specific field extractors
# ──────────────────────────────────────────────────────────────

def _extract_market_id(
    data: dict[str, Any],
    platform: Platform,
) -> str:
    """Extract the platform-specific market ID."""
    if platform == Platform.POLYMARKET:
        return str(data.get("condition_id", data.get("id", "")))
    return str(data.get("ticker", data.get("id", "")))


def _extract_question(
    data: dict[str, Any],
    platform: Platform,
) -> str:
    """Extract the human-readable question."""
    if platform == Platform.POLYMARKET:
        return str(data.get("question", data.get("title", "")))
    return str(data.get("title", data.get("subtitle", "")))


def _extract_volume(
    data: dict[str, Any],
    platform: Platform,
) -> float:
    """Extract total volume in USD."""
    if platform == Platform.POLYMARKET:
        raw: Any = data.get("volume", data.get("volumeNum", 0))
    else:
        raw = data.get("volume", data.get("dollar_volume", 0))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _extract_oi(
    data: dict[str, Any],
    platform: Platform,
) -> float:
    """Extract open interest in USD."""
    if platform == Platform.POLYMARKET:
        raw: Any = data.get("liquidity", data.get("liquidityNum", 0))
    else:
        raw = data.get("open_interest", 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _extract_end_date(
    data: dict[str, Any],
    platform: Platform,
) -> str:
    """Extract market resolution date if available."""
    if platform == Platform.POLYMARKET:
        return str(data.get("end_date_iso", data.get("endDate", "")))
    return str(data.get("close_time", data.get("expiration_time", "")))


def _infer_category(
    data: dict[str, Any],
    platform: Platform,
) -> str:
    """
    Infer macro category from market metadata.

    Args:
        data: Raw market dict.
        platform: Source platform.

    Returns:
        Category string.
    """
    searchable: str = _extract_question(data, platform).lower()
    tags: str = str(data.get("tags", "")).lower()
    combined: str = f"{searchable} {tags}"

    return _classify_text_category(combined)


def _classify_text_category(text: str) -> str:
    """Classify macro category using regex word boundaries."""
    q = text.lower()
    patterns = {
        "fed": r"\b(fed|fomc|rate|federal reserve)\b",
        "sec": r"\b(sec|securities|etf|approval)\b",
        "crypto_regulation": r"\b(cftc|regulation|enforcement|listing)\b",
        "election": r"\b(election|president|candidate|vote)\b",
    }
    for cat, pattern in patterns.items():
        if re.search(pattern, q):
            return cat
    return "general"
