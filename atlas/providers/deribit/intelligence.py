"""CPU-bound options intelligence calculations (Deribit chain snapshots)."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from atlas.providers.deribit.models import (
    OptionsChainSnapshot,
    OptionsIntelligence,
    TermStructurePoint,
)


def calculate_max_pain_price(
    strikes: list[Decimal],
    call_oi: list[Decimal],
    put_oi: list[Decimal],
) -> Decimal:
    """Return strike that minimises total holder loss if expiry settles there."""
    if not strikes:
        return Decimal("0")

    min_loss: Decimal | None = None
    best_strike = strikes[0]
    for candidate in strikes:
        total_loss = Decimal("0")
        for strike, call_open, put_open in zip(strikes, call_oi, put_oi, strict=True):
            if strike > candidate:
                total_loss += (strike - candidate) * call_open
            if strike < candidate:
                total_loss += (candidate - strike) * put_open
        if min_loss is None or total_loss < min_loss:
            min_loss = total_loss
            best_strike = candidate
    return best_strike


def calculate_put_call_ratios(
    call_volume: list[Decimal],
    put_volume: list[Decimal],
    call_oi: list[Decimal],
    put_oi: list[Decimal],
) -> tuple[Decimal, Decimal]:
    """Return (volume PCR, OI PCR); zero denominators yield Decimal zero."""
    vol_call = sum(call_volume, Decimal("0"))
    vol_put = sum(put_volume, Decimal("0"))
    oi_call = sum(call_oi, Decimal("0"))
    oi_put = sum(put_oi, Decimal("0"))
    vol_ratio = _safe_decimal_ratio(vol_put, vol_call)
    oi_ratio = _safe_decimal_ratio(oi_put, oi_call)
    return vol_ratio, oi_ratio


def calculate_iv_skew_atm(
    strikes: list[Decimal],
    iv_call: list[Decimal],
    iv_put: list[Decimal],
    spot_price: Decimal,
) -> Decimal:
    """Approximate 25-delta skew using ATM strike put IV minus call IV."""
    if not strikes or spot_price <= 0:
        return Decimal("0")
    atm_index = min(
        range(len(strikes)),
        key=lambda index: abs(strikes[index] - spot_price),
    )
    put_iv = iv_put[atm_index] if atm_index < len(iv_put) else Decimal("0")
    call_iv = iv_call[atm_index] if atm_index < len(iv_call) else Decimal("0")
    return (put_iv - call_iv) * Decimal("100")


def calculate_term_structure_points(
    expiry_iv_pairs: list[tuple[int, Decimal]],
) -> tuple[list[TermStructurePoint], bool]:
    """Build term structure entries and contango flag from (days, atm_iv) pairs."""
    if not expiry_iv_pairs:
        return [], True

    sorted_pairs = sorted(expiry_iv_pairs, key=lambda pair: pair[0])
    points: list[TermStructurePoint] = []
    for days, atm_iv in sorted_pairs[:4]:
        label = _expiry_label_for_days(days)
        points.append(
            TermStructurePoint(expiry_days=days, atm_iv=atm_iv, expiry_label=label),
        )
    contango = True
    if len(sorted_pairs) >= 2:
        near_iv = sorted_pairs[0][1]
        far_iv = sorted_pairs[-1][1]
        contango = far_iv > near_iv
    return points, contango


def calculate_options_intelligence(
    snapshot: OptionsChainSnapshot,
    expiry_iv_pairs: list[tuple[int, Decimal]] | None = None,
) -> OptionsIntelligence:
    """Derive full intelligence payload from an options chain snapshot."""
    max_pain = calculate_max_pain_price(
        snapshot.strikes,
        snapshot.call_oi,
        snapshot.put_oi,
    )
    vol_pcr, oi_pcr = calculate_put_call_ratios(
        snapshot.call_volume_24h,
        snapshot.put_volume_24h,
        snapshot.call_oi,
        snapshot.put_oi,
    )
    iv_skew = calculate_iv_skew_atm(
        snapshot.strikes,
        snapshot.iv_call,
        snapshot.iv_put,
        snapshot.spot_price,
    )
    pairs = expiry_iv_pairs or []
    term_structure, contango = calculate_term_structure_points(pairs)
    spot_pct = _spot_to_max_pain_pct(snapshot.spot_price, max_pain)
    return OptionsIntelligence(
        asset=snapshot.asset,
        max_pain_price=max_pain,
        put_call_ratio_volume=vol_pcr,
        put_call_ratio_oi=oi_pcr,
        term_structure=term_structure,
        iv_skew=iv_skew,
        contango=contango,
        spot_to_max_pain_pct=spot_pct,
    )


def _spot_to_max_pain_pct(spot: Decimal, max_pain: Decimal) -> Decimal:
    if spot <= 0:
        return Decimal("0")
    return ((spot - max_pain) / spot) * Decimal("100")


def _safe_decimal_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    if denominator <= 0:
        return Decimal("0")
    return numerator / denominator


def _expiry_label_for_days(days: int) -> str:
    if days <= 10:
        return "1W"
    if days <= 21:
        return "2W"
    if days <= 45:
        return "1M"
    return "3M"


def utc_now_iso() -> str:
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
