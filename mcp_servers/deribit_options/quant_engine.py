"""Quantitative engine for options analytics.

Section 26.3 Architecture — Options Skew Detector.
Pure computation module — no I/O, no async, fully deterministic.
Processes ticker data from the State Cache to produce 25-delta
risk reversals, IV surfaces, P/C ratios, and block trade summaries.

Sentinel Invariants:
  - No stdlib json, no pandas, no os.getenv
  - Decimal for financial values (strike prices, notional)
  - float for dimensionless values (IV, delta, ratios)
  - 40-line function cap enforced
  - Loguru positional format logging
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
from loguru import logger

from .models import (
    ATMTermEntry,
    BlockTrade,
    BlockTradesResponse,
    IVSurfacePoint,
    IVSurfaceResponse,
    OptionsSkewResponse,
    PutCallRatioResponse,
    SkewEntry,
)
from .state_manager import DeribitStateCache


# Skew label thresholds (dimensionless IV difference)
_BULLISH_SKEW_THRESHOLD = 0.02
_BEARISH_SKEW_THRESHOLD = -0.02

# Standard expiry buckets in days
_STANDARD_EXPIRY_BUCKETS = {
    "7d": (3, 10),
    "30d": (20, 45),
    "90d": (60, 120),
    "180d": (150, 210),
}


def calculate_options_skew(
    cache: DeribitStateCache,
    coin: str,
) -> OptionsSkewResponse:
    """Calculate 25-delta risk reversal term structure.

    Section 26.3: Primary output for institutional directional
    conviction gauge. Interpolates 25-delta call and put IVs
    across standard expiry buckets.

    Args:
        cache: Live state cache with ticker data.
        coin: Asset symbol (BTC or ETH).

    Returns:
        OptionsSkewResponse with term structure entries.
    """
    tickers = cache.get_tickers_for_coin(coin)
    if not tickers:
        return _empty_skew_response(coin, cache)

    entries = _build_skew_entries(tickers, coin)
    return OptionsSkewResponse(
        coin=coin.upper(),
        entries=sorted(entries, key=lambda e: e.days_to_expiry),
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _build_skew_entries(
    tickers: list[dict],
    coin: str,
) -> list[SkewEntry]:
    """Build skew entries for each standard expiry bucket.

    Groups tickers by expiry, then interpolates 25-delta IVs
    for each bucket that has sufficient data.

    Args:
        tickers: List of raw ticker dicts from cache.
        coin: Asset symbol for context.

    Returns:
        List of SkewEntry objects for matched expiry buckets.
    """
    by_expiry = _group_tickers_by_expiry(tickers)
    entries: list[SkewEntry] = []

    for label, (min_dte, max_dte) in _STANDARD_EXPIRY_BUCKETS.items():
        entry = _find_best_expiry_for_bucket(
            by_expiry, label, min_dte, max_dte,
        )
        if entry is not None:
            entries.append(entry)

    return entries


def _group_tickers_by_expiry(
    tickers: list[dict],
) -> dict[str, list[dict]]:
    """Group ticker dicts by their expiry date string.

    Args:
        tickers: List of raw ticker dicts.

    Returns:
        Dict mapping expiry date strings to ticker lists.
    """
    by_expiry: dict[str, list[dict]] = {}
    for t in tickers:
        instrument = t.get("instrument_name", "")
        parts = instrument.split("-")
        if len(parts) >= 3:
            expiry_str = parts[1]
            by_expiry.setdefault(expiry_str, []).append(t)
    return by_expiry


def _find_best_expiry_for_bucket(
    by_expiry: dict[str, list[dict]],
    label: str,
    min_dte: int,
    max_dte: int,
) -> SkewEntry | None:
    """Find the best matching expiry for a standard bucket.

    Scans all expiry groups, selects the one whose DTE falls
    within [min_dte, max_dte], and interpolates 25-delta IVs.

    Args:
        by_expiry: Tickers grouped by expiry string.
        label: Bucket label (e.g. '7d', '30d').
        min_dte: Minimum days to expiry for this bucket.
        max_dte: Maximum days to expiry for this bucket.

    Returns:
        SkewEntry if a matching expiry is found, None otherwise.
    """
    for expiry_str, expiry_tickers in by_expiry.items():
        dte = _calculate_dte(expiry_str)
        if dte is None or not (min_dte <= dte <= max_dte):
            continue
        return _compute_skew_for_expiry(
            expiry_tickers, expiry_str, dte, label,
        )
    return None


def _compute_skew_for_expiry(
    tickers: list[dict],
    expiry_str: str,
    dte: int,
    label: str,
) -> SkewEntry | None:
    """Compute 25-delta risk reversal for a single expiry.

    Separates calls and puts, interpolates 25-delta IVs,
    and builds the SkewEntry.

    Args:
        tickers: Tickers for this specific expiry.
        expiry_str: Deribit expiry date string.
        dte: Days to expiry.
        label: Bucket label for display.

    Returns:
        SkewEntry or None if insufficient data.
    """
    calls, puts = _separate_calls_puts(tickers)
    call_iv = _interpolate_25d_iv(calls, target_delta=0.25)
    put_iv = _interpolate_25d_iv(puts, target_delta=-0.25)

    if call_iv is None or put_iv is None:
        return None

    rr = call_iv - put_iv
    return SkewEntry(
        expiry_label=label,
        expiry_date=expiry_str,
        days_to_expiry=dte,
        call_iv_25d=round(call_iv, 6),
        put_iv_25d=round(put_iv, 6),
        risk_reversal=round(rr, 6),
        skew_label=_classify_skew(rr),
    )


def _separate_calls_puts(
    tickers: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Separate tickers into calls and puts.

    Uses the last character of the instrument name (C or P).

    Args:
        tickers: List of ticker dicts for one expiry.

    Returns:
        Tuple of (calls, puts) ticker lists.
    """
    calls: list[dict] = []
    puts: list[dict] = []
    for t in tickers:
        instrument = t.get("instrument_name", "")
        if instrument.endswith("-C"):
            calls.append(t)
        elif instrument.endswith("-P"):
            puts.append(t)
    return calls, puts


def _interpolate_25d_iv(
    options: list[dict],
    target_delta: float,
) -> float | None:
    """Interpolate IV at a target delta using numpy linear interpolation.

    Deribit provides greeks.delta in ticker payloads. We find the
    two options straddling the target delta and linearly interpolate.

    Args:
        options: List of option ticker dicts (all calls or all puts).
        target_delta: Target delta value (0.25 for calls, -0.25 for puts).

    Returns:
        Interpolated IV at target delta, or None if insufficient data.
    """
    points = _extract_delta_iv_points(options)
    if len(points) < 2:
        return None

    deltas = np.array([p[0] for p in points])
    ivs = np.array([p[1] for p in points])

    sort_idx = np.argsort(deltas)
    deltas = deltas[sort_idx]
    ivs = ivs[sort_idx]

    return float(np.interp(target_delta, deltas, ivs))


def _extract_delta_iv_points(
    options: list[dict],
) -> list[tuple[float, float]]:
    """Extract (delta, mark_iv) pairs from option tickers.

    Filters out options with missing or zero greeks.

    Args:
        options: List of option ticker dicts.

    Returns:
        List of (delta, mark_iv) tuples.
    """
    points: list[tuple[float, float]] = []
    for opt in options:
        greeks = opt.get("greeks", {})
        if not greeks:
            continue
        delta = greeks.get("delta")
        mark_iv = opt.get("mark_iv")
        if delta is not None and mark_iv is not None and mark_iv > 0:
            points.append((float(delta), float(mark_iv)))
    return points


def _classify_skew(risk_reversal: float) -> str:
    """Classify risk reversal into a contextual label.

    Args:
        risk_reversal: 25d RR value (call IV - put IV).

    Returns:
        'Bullish Skew', 'Bearish Skew', or 'Neutral'.
    """
    if risk_reversal > _BULLISH_SKEW_THRESHOLD:
        return "Bullish Skew"
    if risk_reversal < _BEARISH_SKEW_THRESHOLD:
        return "Bearish Skew"
    return "Neutral"


# ---------------------------------------------------------------------------
# IV Surface
# ---------------------------------------------------------------------------

def calculate_iv_surface(
    cache: DeribitStateCache,
    coin: str,
) -> IVSurfaceResponse:
    """Calculate full implied volatility surface snapshot.

    Section 26.3: Provides term structure shape detection for
    the DerivativesAgent.

    Args:
        cache: Live state cache with ticker data.
        coin: Asset symbol (BTC or ETH).

    Returns:
        IVSurfaceResponse with surface grid and ATM term structure.
    """
    tickers = cache.get_tickers_for_coin(coin)
    underlying = cache.get_underlying_price(coin)

    if not tickers:
        return _empty_iv_surface_response(coin, cache, underlying)

    surface_points = _build_surface_points(tickers)
    atm_entries = _build_atm_term_structure(tickers, underlying)
    shape = _detect_term_structure_shape(atm_entries)

    return IVSurfaceResponse(
        coin=coin.upper(),
        surface_points=surface_points,
        atm_term_structure=sorted(
            atm_entries, key=lambda e: e.days_to_expiry,
        ),
        term_structure_shape=shape,
        underlying_price=underlying,
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _build_surface_points(
    tickers: list[dict],
) -> list[IVSurfacePoint]:
    """Build IV surface grid points from tickers.

    Args:
        tickers: List of raw ticker dicts.

    Returns:
        List of IVSurfacePoint objects.
    """
    points: list[IVSurfacePoint] = []
    for t in tickers:
        point = _ticker_to_surface_point(t)
        if point is not None:
            points.append(point)
    return points


def _ticker_to_surface_point(t: dict) -> IVSurfacePoint | None:
    """Convert a single ticker dict to an IVSurfacePoint.

    Args:
        t: Raw ticker dict from Deribit.

    Returns:
        IVSurfacePoint or None if data is incomplete.
    """
    instrument = t.get("instrument_name", "")
    mark_iv = t.get("mark_iv")
    if not instrument or mark_iv is None:
        return None

    parts = instrument.split("-")
    if len(parts) < 4:
        return None

    option_type = "call" if parts[3] == "C" else "put"
    return IVSurfacePoint(
        expiry_date=parts[1],
        strike_price=Decimal(parts[2]),
        mark_iv=float(mark_iv),
        option_type=option_type,
    )


def _build_atm_term_structure(
    tickers: list[dict],
    underlying: Decimal,
) -> list[ATMTermEntry]:
    """Build ATM IV term structure across expiries.

    For each expiry, finds the option with strike closest to
    the underlying price and uses its mark IV.

    Args:
        tickers: List of raw ticker dicts.
        underlying: Current underlying index price.

    Returns:
        List of ATMTermEntry objects.
    """
    by_expiry = _group_tickers_by_expiry(tickers)
    entries: list[ATMTermEntry] = []

    for expiry_str, expiry_tickers in by_expiry.items():
        entry = _compute_atm_for_expiry(
            expiry_tickers, expiry_str, underlying,
        )
        if entry is not None:
            entries.append(entry)

    return entries


def _compute_atm_for_expiry(
    tickers: list[dict],
    expiry_str: str,
    underlying: Decimal,
) -> ATMTermEntry | None:
    """Compute ATM IV for a single expiry.

    Args:
        tickers: Tickers for this expiry.
        expiry_str: Deribit expiry date string.
        underlying: Current underlying index price.

    Returns:
        ATMTermEntry or None if no valid data.
    """
    dte = _calculate_dte(expiry_str)
    if dte is None:
        return None

    atm_iv = _find_atm_iv(tickers, underlying)
    if atm_iv is None:
        return None

    return ATMTermEntry(
        expiry_date=expiry_str,
        days_to_expiry=dte,
        atm_iv=round(atm_iv, 6),
    )


def _find_atm_iv(
    tickers: list[dict],
    underlying: Decimal,
) -> float | None:
    """Find the ATM implied volatility (closest strike to underlying).

    Args:
        tickers: List of ticker dicts for one expiry.
        underlying: Current underlying price.

    Returns:
        Mark IV of the ATM option, or None.
    """
    if underlying == Decimal("0"):
        return None

    best_iv: float | None = None
    best_distance = Decimal("Infinity")

    for t in tickers:
        instrument = t.get("instrument_name", "")
        parts = instrument.split("-")
        if len(parts) < 4:
            continue

        strike = Decimal(parts[2])
        distance = abs(strike - underlying)
        mark_iv = t.get("mark_iv")

        if distance < best_distance and mark_iv is not None:
            best_distance = distance
            best_iv = float(mark_iv)

    return best_iv


def _detect_term_structure_shape(
    atm_entries: list[ATMTermEntry],
) -> str:
    """Detect whether the ATM term structure is in contango or backwardation.

    Contango: short-term IV < long-term IV (normal).
    Backwardation: short-term IV > long-term IV (inverted).

    Args:
        atm_entries: Sorted ATM term structure entries.

    Returns:
        'Contango', 'Backwardation', or 'Flat'.
    """
    if len(atm_entries) < 2:
        return "Flat"

    sorted_entries = sorted(atm_entries, key=lambda e: e.days_to_expiry)
    short_iv = sorted_entries[0].atm_iv
    long_iv = sorted_entries[-1].atm_iv
    diff = long_iv - short_iv

    if diff > 0.01:
        return "Contango"
    if diff < -0.01:
        return "Backwardation"
    return "Flat"


# ---------------------------------------------------------------------------
# Put/Call Ratios
# ---------------------------------------------------------------------------

def calculate_put_call_ratio(
    cache: DeribitStateCache,
    coin: str,
) -> PutCallRatioResponse:
    """Calculate aggregate Put/Call ratios from ticker data.

    Section 26.3: Volume and OI P/C ratios for sentiment gauging.

    Args:
        cache: Live state cache with ticker data.
        coin: Asset symbol (BTC or ETH).

    Returns:
        PutCallRatioResponse with volume and OI ratios.
    """
    tickers = cache.get_tickers_for_coin(coin)
    if not tickers:
        return _empty_pc_ratio_response(coin, cache)

    stats = _aggregate_volume_oi(tickers)
    return PutCallRatioResponse(
        coin=coin.upper(),
        volume_put_call_ratio=_safe_ratio(
            stats["put_vol"], stats["call_vol"],
        ),
        oi_put_call_ratio=_safe_ratio(
            stats["put_oi"], stats["call_oi"],
        ),
        total_call_volume=stats["call_vol"],
        total_put_volume=stats["put_vol"],
        total_call_oi=stats["call_oi"],
        total_put_oi=stats["put_oi"],
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _aggregate_volume_oi(
    tickers: list[dict],
) -> dict[str, float]:
    """Sum 24h volume and open interest for calls and puts.

    Args:
        tickers: List of raw ticker dicts.

    Returns:
        Dict with call_vol, put_vol, call_oi, put_oi.
    """
    stats = {
        "call_vol": 0.0,
        "put_vol": 0.0,
        "call_oi": 0.0,
        "put_oi": 0.0,
    }
    for t in tickers:
        instrument = t.get("instrument_name", "")
        volume = float(t.get("stats", {}).get("volume", 0) or 0)
        oi = float(t.get("open_interest", 0) or 0)

        if instrument.endswith("-C"):
            stats["call_vol"] += volume
            stats["call_oi"] += oi
        elif instrument.endswith("-P"):
            stats["put_vol"] += volume
            stats["put_oi"] += oi

    return stats


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Compute a safe division ratio, returning 0.0 on zero denominator.

    Args:
        numerator: Numerator value.
        denominator: Denominator value.

    Returns:
        Ratio rounded to 4 decimal places, or 0.0 if denominator is 0.
    """
    if denominator == 0.0:
        return 0.0
    return round(numerator / denominator, 4)


# ---------------------------------------------------------------------------
# Block Trades
# ---------------------------------------------------------------------------

def calculate_block_trades(
    cache: DeribitStateCache,
    coin: str,
    min_notional_usd: Decimal,
) -> BlockTradesResponse:
    """Retrieve filtered block trades from the state cache.

    Section 26.3: Rolling buffer of institutional block trades
    for aggressive buying/selling pattern detection.

    Args:
        cache: Live state cache with block trade data.
        coin: Asset symbol (BTC or ETH).
        min_notional_usd: Minimum USD notional filter.

    Returns:
        BlockTradesResponse with filtered block trades.
    """
    raw_trades = cache.get_block_trades(coin, min_notional_usd)
    trades = [_raw_to_block_trade(t) for t in raw_trades]
    valid_trades = [t for t in trades if t is not None]

    return BlockTradesResponse(
        coin=coin.upper(),
        trades=valid_trades,
        total_count=len(valid_trades),
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _raw_to_block_trade(raw: dict) -> BlockTrade | None:
    """Convert a raw trade dict to a BlockTrade model.

    Args:
        raw: Raw trade dict from state cache.

    Returns:
        BlockTrade model or None if data is incomplete.
    """
    instrument = raw.get("instrument_name", "")
    parts = instrument.split("-")
    if len(parts) < 4:
        return None

    timestamp_ms = raw.get("timestamp", 0)
    ts_str = _ms_to_iso(timestamp_ms)

    return BlockTrade(
        instrument_name=instrument,
        direction=raw.get("direction", "unknown"),
        amount=float(raw.get("amount", 0)),
        price=Decimal(str(raw.get("price", 0))),
        notional_usd=Decimal(raw.get("notional_usd", "0")),
        mark_iv=float(raw.get("mark_iv", 0) or 0),
        timestamp=ts_str,
        option_type="call" if parts[3] == "C" else "put",
        strike_price=Decimal(parts[2]),
        expiry_date=parts[1],
    )


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _calculate_dte(expiry_str: str) -> int | None:
    """Calculate days to expiry from a Deribit date string.

    Deribit uses format like '30MAY25' (DDMMMYY).

    Args:
        expiry_str: Deribit expiry date string (e.g. '30MAY25').

    Returns:
        Days to expiry, or None if parsing fails.
    """
    try:
        expiry_date = datetime.strptime(expiry_str, "%d%b%y").replace(
            tzinfo=timezone.utc,
        )
        now = datetime.now(timezone.utc)
        delta = (expiry_date - now).days
        return max(delta, 0)
    except (ValueError, TypeError):
        logger.debug("Failed to parse expiry date | expiry={}", expiry_str)
        return None


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string.

    Returns:
        ISO-8601 formatted UTC timestamp.
    """
    return datetime.now(timezone.utc).isoformat()


def _ms_to_iso(timestamp_ms: int | float) -> str:
    """Convert millisecond timestamp to ISO-8601 string.

    Args:
        timestamp_ms: Unix timestamp in milliseconds.

    Returns:
        ISO-8601 formatted UTC timestamp.
    """
    try:
        dt = datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc,
        )
        return dt.isoformat()
    except (ValueError, TypeError, OSError):
        logger.debug("Failed to convert timestamp | ts_ms={}", timestamp_ms)
        return _now_iso()


# ---------------------------------------------------------------------------
# Empty response factories (fault tolerance)
# ---------------------------------------------------------------------------

def _empty_skew_response(
    coin: str,
    cache: DeribitStateCache,
) -> OptionsSkewResponse:
    """Build an empty skew response for graceful degradation.

    Args:
        coin: Asset symbol.
        cache: State cache for WS status.

    Returns:
        OptionsSkewResponse with empty entries.
    """
    return OptionsSkewResponse(
        coin=coin.upper(),
        entries=[],
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _empty_iv_surface_response(
    coin: str,
    cache: DeribitStateCache,
    underlying: Decimal,
) -> IVSurfaceResponse:
    """Build an empty IV surface response for graceful degradation.

    Args:
        coin: Asset symbol.
        cache: State cache for WS status.
        underlying: Current underlying price.

    Returns:
        IVSurfaceResponse with empty surface.
    """
    return IVSurfaceResponse(
        coin=coin.upper(),
        surface_points=[],
        atm_term_structure=[],
        term_structure_shape="Flat",
        underlying_price=underlying,
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )


def _empty_pc_ratio_response(
    coin: str,
    cache: DeribitStateCache,
) -> PutCallRatioResponse:
    """Build an empty P/C ratio response for graceful degradation.

    Args:
        coin: Asset symbol.
        cache: State cache for WS status.

    Returns:
        PutCallRatioResponse with zero ratios.
    """
    return PutCallRatioResponse(
        coin=coin.upper(),
        volume_put_call_ratio=0.0,
        oi_put_call_ratio=0.0,
        total_call_volume=0.0,
        total_put_volume=0.0,
        total_call_oi=0.0,
        total_put_oi=0.0,
        snapshot_ts=_now_iso(),
        ws_status=cache.get_ws_status(),
    )
