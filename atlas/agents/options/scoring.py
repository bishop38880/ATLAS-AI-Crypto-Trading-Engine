"""Scoring helpers for OptionsIntelligenceAgent feature buckets."""

from __future__ import annotations

from decimal import Decimal

from atlas.providers.deribit.models import OptionsIntelligence


def calculate_spot_vs_max_pain_points(
    intelligence: OptionsIntelligence,
    is_long_eval: bool | None,
) -> int:
    """Score spot vs max pain (up to 5 pts) when eval direction matches signal."""
    pct = intelligence.spot_to_max_pain_pct
    magnitude = abs(pct)
    if magnitude <= Decimal("2"):
        return 2
    if magnitude <= Decimal("5"):
        directional_pts = 3
    else:
        directional_pts = 5

    if pct > Decimal("2"):
        bearish_signal = True
    elif pct < Decimal("-2"):
        bearish_signal = False
    else:
        return 2

    if is_long_eval is None:
        return 0
    if is_long_eval and not bearish_signal:
        return directional_pts
    if not is_long_eval and bearish_signal:
        return directional_pts
    return 0


def calculate_put_call_ratio_points(
    intelligence: OptionsIntelligence,
    is_long_eval: bool | None,
) -> int:
    """Score 24h put/call volume ratio (up to 6 pts, contrarian)."""
    pcr = intelligence.put_call_ratio_volume
    if is_long_eval is None:
        return 0
    if is_long_eval:
        if pcr > Decimal("1.5"):
            return 6
        if pcr > Decimal("1.2"):
            return 4
        if pcr < Decimal("0.8"):
            return 0
        return 0
    if pcr < Decimal("0.5"):
        return 6
    if pcr < Decimal("0.8"):
        return 4
    return 0


def calculate_iv_skew_points(
    intelligence: OptionsIntelligence,
    is_long_eval: bool | None,
) -> int:
    """Score IV skew in vol points (up to 4 pts)."""
    skew = intelligence.iv_skew
    if is_long_eval is None:
        return 0
    if not is_long_eval:
        if skew > Decimal("5"):
            return 4
        if skew > Decimal("2"):
            return 2
        if skew >= Decimal("-2"):
            return 0
        return 0
    if skew < Decimal("-2"):
        return 2
    if skew <= Decimal("2"):
        return 0
    return 0


def calculate_term_structure_points(
    intelligence: OptionsIntelligence,
) -> int:
    """Backwardation amplifies stress — 3 pts; contango — 1 pt."""
    if not intelligence.contango:
        return 3
    return 1


def resolve_eval_side(context: dict[str, object]) -> bool | None:
    """Map context trade bias to long (True), short (False), or unknown (None)."""
    bias_raw = (
        context.get("trade_bias")
        or context.get("evaluation_bias")
        or context.get("proposed_direction")
        or context.get("direction")
    )
    if bias_raw is None:
        return None
    bias = str(bias_raw).lower()
    if bias in ("long", "bullish", "buy"):
        return True
    if bias in ("short", "bearish", "sell"):
        return False
    return None
