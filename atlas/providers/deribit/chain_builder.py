"""Build OptionsChainSnapshot from Deribit book-summary REST payloads."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from atlas.providers.deribit.intelligence import utc_now_iso
from atlas.providers.deribit.models import OptionsChainSnapshot


def build_chain_snapshot_from_book_summaries(
    asset: str,
    summaries: list[dict[str, Any]],
) -> OptionsChainSnapshot:
    """Aggregate per-instrument book summaries into one chain snapshot."""
    strike_map = _aggregate_strikes_by_side(summaries)
    spot = _extract_spot_price(summaries)
    strikes_sorted = sorted(strike_map.keys())
    return OptionsChainSnapshot(
        asset=asset.upper(),
        expiry="ALL",
        strikes=strikes_sorted,
        call_oi=[strike_map[s]["call_oi"] for s in strikes_sorted],
        put_oi=[strike_map[s]["put_oi"] for s in strikes_sorted],
        call_volume_24h=[strike_map[s]["call_vol"] for s in strikes_sorted],
        put_volume_24h=[strike_map[s]["put_vol"] for s in strikes_sorted],
        iv_call=[strike_map[s]["call_iv"] for s in strikes_sorted],
        iv_put=[strike_map[s]["put_iv"] for s in strikes_sorted],
        spot_price=spot,
        timestamp_utc=utc_now_iso(),
    )


def build_expiry_iv_pairs(
    summaries: list[dict[str, Any]],
    spot_price: Decimal,
) -> list[tuple[int, Decimal]]:
    """Collect (days_to_expiry, atm_iv) for term-structure computation."""
    by_expiry: dict[str, list[dict[str, Any]]] = {}
    for row in summaries:
        parsed = _parse_instrument_name(str(row.get("instrument_name", "")))
        if parsed is None:
            continue
        expiry_key, _strike, _side = parsed
        by_expiry.setdefault(expiry_key, []).append(row)

    pairs: list[tuple[int, Decimal]] = []
    for expiry_key, rows in by_expiry.items():
        days = _days_to_expiry(expiry_key)
        if days is None:
            continue
        atm_iv = _atm_iv_for_expiry(rows, spot_price)
        if atm_iv is not None:
            pairs.append((days, atm_iv))
    return pairs


def _aggregate_strikes_by_side(
    summaries: list[dict[str, Any]],
) -> dict[Decimal, dict[str, Decimal]]:
    """Sum OI, volume, and IV by strike and option side."""
    strike_map: dict[Decimal, dict[str, Decimal]] = {}
    for row in summaries:
        parsed = _parse_instrument_name(str(row.get("instrument_name", "")))
        if parsed is None:
            continue
        _expiry, strike, side = parsed
        bucket = strike_map.setdefault(
            strike,
            {
                "call_oi": Decimal("0"),
                "put_oi": Decimal("0"),
                "call_vol": Decimal("0"),
                "put_vol": Decimal("0"),
                "call_iv": Decimal("0"),
                "put_iv": Decimal("0"),
            },
        )
        oi = _decimal_from_field(row.get("open_interest"))
        volume = _decimal_from_field(row.get("volume"))
        mark_iv = _decimal_from_field(row.get("mark_iv"))
        if side == "C":
            bucket["call_oi"] += oi
            bucket["call_vol"] += volume
            if mark_iv > bucket["call_iv"]:
                bucket["call_iv"] = mark_iv
        else:
            bucket["put_oi"] += oi
            bucket["put_vol"] += volume
            if mark_iv > bucket["put_iv"]:
                bucket["put_iv"] = mark_iv
    return strike_map


def _extract_spot_price(summaries: list[dict[str, Any]]) -> Decimal:
    for row in summaries:
        underlying = row.get("underlying_price")
        if underlying is not None:
            return _decimal_from_field(underlying)
    return Decimal("0")


def _atm_iv_for_expiry(
    rows: list[dict[str, Any]],
    spot_price: Decimal,
) -> Decimal | None:
    """Mean mark IV for instruments within 5% of spot (calls and puts)."""
    if spot_price <= 0:
        return None
    band = spot_price * Decimal("0.05")
    iv_samples: list[Decimal] = []
    for row in rows:
        parsed = _parse_instrument_name(str(row.get("instrument_name", "")))
        if parsed is None:
            continue
        _expiry, strike, _side = parsed
        if abs(strike - spot_price) <= band:
            mark_iv = _decimal_from_field(row.get("mark_iv"))
            if mark_iv > 0:
                iv_samples.append(mark_iv)
    if not iv_samples:
        return None
    return sum(iv_samples, Decimal("0")) / Decimal(len(iv_samples))


def _parse_instrument_name(
    instrument_name: str,
) -> tuple[str, Decimal, str] | None:
    """Parse Deribit option name e.g. BTC-28MAR25-84000-C."""
    parts = instrument_name.split("-")
    if len(parts) < 4:
        return None
    expiry = parts[1]
    try:
        strike = Decimal(parts[2])
    except Exception:
        return None
    side = parts[3].upper()
    if side not in ("C", "P"):
        return None
    return expiry, strike, side


def _days_to_expiry(expiry_str: str) -> int | None:
    """Convert Deribit expiry token (DDMMMYY) to days from today."""
    try:
        expiry_date = datetime.strptime(expiry_str.upper(), "%d%b%y").date()
    except ValueError:
        return None
    today = datetime.now(timezone.utc).date()
    delta = (expiry_date - today).days
    return max(delta, 0)


def _decimal_from_field(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")
