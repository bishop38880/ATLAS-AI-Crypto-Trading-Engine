"""Funding command centre aggregation — Redis OKX MCP cache reads only.

Historical funding bars are sourced from ``provider:okx_mcp:{ASSET}:funding_history``
(see ``atlas.providers.okx_mcp.cache``). Assets use compact symbols (``BTCUSDT``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Literal

import msgspec
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from atlas.api.schemas import _BaseConfig
from backend.config.asset_universe import POLARIS_DASHBOARD_FALLBACK_BASES_ORDER as _FALLBACK_ACTIVE_33_BASES

_FLIP_KIND = Literal["COMPRESS_FROM_POSITIVE", "COMPRESS_FROM_NEGATIVE", "NEUTRAL"]

_TARGET_CARD_COUNT = 33
_PERIOD_PER_DAY = Decimal("3")
_DAYS_YEAR = Decimal("365")
_OKX_PREFIX = "provider:okx_mcp"


def derive_pair_from_rotation_entry(raw: str) -> str:
    """Normalize rotation SMEMBERS values to ``BASE/USDT`` (Polaris convention)."""

    trimmed = raw.strip().upper()
    if "/" in trimmed:
        base, quote = trimmed.split("/", 1)
        return f"{base}/{quote}"
    if trimmed.endswith("-PERP"):
        return f"{trimmed[:-len('-PERP')]}/USDT"
    if trimmed.endswith("USDT") and trimmed != "USDT":
        return f"{trimmed[:-len('USDT')]}/USDT"
    return f"{trimmed}/USDT"


def pair_to_compact_exchange(pair: str) -> str:
    """``BTC/USDT`` → ``BTCUSDT``."""

    return pair.replace("/", "").strip().upper()


def build_dashboard_33_pairs(active_from_redis: Iterable[str]) -> list[str]:
    """Match the POLARIS 33-card ladder (rotation first, canonical fallback tops up)."""

    out: list[str] = []
    seen: set[str] = set()
    for entry in active_from_redis:
        normalized = derive_pair_from_rotation_entry(entry)
        bucket = normalized.upper()
        if bucket not in seen:
            seen.add(bucket)
            out.append(normalized)
        if len(out) >= _TARGET_CARD_COUNT:
            return out[:_TARGET_CARD_COUNT]

    for base in _FALLBACK_ACTIVE_33_BASES:
        pair = f"{base}/USDT"
        bucket = pair.upper()
        if bucket not in seen:
            seen.add(bucket)
            out.append(pair)
        if len(out) >= _TARGET_CARD_COUNT:
            break

    return out[:_TARGET_CARD_COUNT]


def annualized_simple_funding_pct(rate_per_8h: Decimal) -> Decimal:
    """Simple annualisation: 8h rate × 3 × 365 × 100 (percentage points).

    Matches common perp dashboards; not continuously compounded.
    """

    return rate_per_8h * _PERIOD_PER_DAY * _DAYS_YEAR * Decimal("100")


def calculate_funding_zscore(current: Decimal, historical: list[Decimal]) -> float:
    """Vs distribution of prior observations (excluding the newest bar when possible)."""

    if not historical:
        return 0.0
    if len(historical) == 1:
        return 0.0

    prior = historical[:-1]
    xs = [float(x) for x in prior]
    mean_val = sum(xs) / len(xs)
    variance = sum((x - mean_val) ** 2 for x in xs) / len(xs)
    std = variance**0.5
    if std < 1e-12:
        return 0.0
    return (float(current) - mean_val) / std


def classify_funding_flip(zscore: float) -> tuple[_FLIP_KIND, int, str]:
    """Mean-reversion hint on the *funding rate* (not spot direction)."""

    if zscore != zscore:  # NaN guard
        return ("NEUTRAL", 0, "Insufficient dispersion in history.")

    magnitude = abs(zscore)
    strength = int(min(100.0, max(0.0, (magnitude / 3.0) * 100.0)))

    if zscore >= 2.5:
        return (
            "COMPRESS_FROM_POSITIVE",
            strength,
            "Extreme vs 30d — positive funding stretched; historically tends to soften.",
        )
    if zscore <= -2.5:
        return (
            "COMPRESS_FROM_NEGATIVE",
            strength,
            "Extreme vs 30d — negative funding stretched; historically tends to mean-revert",
        )

    if zscore >= 1.8:
        return (
            "COMPRESS_FROM_POSITIVE",
            min(strength, 55),
            "Elevated vs 30d — watch for long crowding unwind.",
        )
    if zscore <= -1.8:
        return (
            "COMPRESS_FROM_NEGATIVE",
            min(strength, 55),
            "Compressed vs 30d — crowded short funding may tighten.",
        )

    return ("NEUTRAL", 0, "Inside normal range vs recent 8h prints.")


@dataclass(frozen=True)
class _OkxBar:
    funding_time_ms: int
    funding_rate: Decimal


def _decode_funding_history(raw: bytes | None) -> list[_OkxBar] | None:
    if raw is None:
        return None
    try:
        payload = msgspec.json.decode(raw)
    except Exception:
        return None
    bars_raw = payload.get("bars") if isinstance(payload, dict) else None
    if not isinstance(bars_raw, list):
        return None
    out: list[_OkxBar] = []
    for item in bars_raw:
        if not isinstance(item, dict):
            continue
        try:
            rate = Decimal(str(item.get("funding_rate")))
            fts = int(item.get("funding_time", 0))
        except Exception:
            continue
        out.append(_OkxBar(funding_time_ms=fts, funding_rate=rate))
    return out


def _decode_funding_rate(raw: bytes | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        payload = msgspec.json.decode(raw)
        if not isinstance(payload, dict):
            return None
        return Decimal(str(payload.get("funding_rate", "0")))
    except Exception:
        return None


class FundingHistoryPoint(BaseModel):
    """Single 8-hour funding observation."""

    model_config = _BaseConfig

    ts: str = Field(description="Funding timestamp ISO-8601 UTC.")
    funding_time_ms: int = Field(description="Original exchange ms.")
    rate: str = Field(description="Decimal string — per-interval funding rate.")


class FundingCommandRow(BaseModel):
    """Row for dashboard grid."""

    model_config = _BaseConfig

    asset: str = Field(description="``BASE/USDT`` pair.")
    compact_symbol: str = Field(description="``BASEUSDT`` cache key suffix.")
    funding_rate_8h: str = Field(description="Latest per-interval funding rate.")
    annualized_simple_pct: str = Field(description="Simple annualised % (not compounded).")
    zscore: float = Field(description="Z vs trailing 30d 8h history.")
    flip_signal: _FLIP_KIND
    flip_strength: int = Field(ge=0, le=100)
    flip_explanation: str
    effective_long_carry_pct: str = Field(
        description="Signed simple APY — long pays when positive.",
    )
    effective_short_carry_pct: str = Field(
        description="Signed simple APY — short earns when funding positive.",
    )
    history: list[FundingHistoryPoint]
    cache_hit: bool = Field(description="True when Redis held both rate + history.")


class FundingCommandCenterPayload(BaseModel):
    """REST envelope for Funding Command Centre."""

    model_config = _BaseConfig

    generated_at: str = Field(description="UTC ISO envelope timestamp.")
    rows: list[FundingCommandRow]
    dashboard_pairs_used: list[str]


async def fetch_funding_command_center_snapshot(redis: Redis) -> FundingCommandCenterPayload:
    """Build per-asset funding snapshot from OKX MCP Redis caches."""

    redis_active_members = await redis.smembers("polaris:rotation:active_33")
    active_list: list[str] = []
    if redis_active_members:
        for raw_member in redis_active_members:
            if isinstance(raw_member, bytes):
                active_list.append(raw_member.decode("utf-8"))
            else:
                active_list.append(str(raw_member))

    pairs = build_dashboard_33_pairs(sorted(active_list))
    if not pairs:
        return FundingCommandCenterPayload(
            generated_at=datetime.now(timezone.utc).isoformat(),
            rows=[],
            dashboard_pairs_used=[],
        )

    compact_keys = [pair_to_compact_exchange(p) for p in pairs]

    pipe = redis.pipeline()
    for compact in compact_keys:
        pipe.get(f"{_OKX_PREFIX}:{compact}:funding_rate")
        pipe.get(f"{_OKX_PREFIX}:{compact}:funding_history")
    raw_chunks = await pipe.execute()

    row_builders: list[tuple[float, str, FundingCommandRow]] = []
    chunks = raw_chunks if isinstance(raw_chunks, list) else []

    idx = 0
    for pair, compact in zip(pairs, compact_keys, strict=True):
        rate_raw = chunks[idx]
        hist_raw = chunks[idx + 1]
        idx += 2

        rate_dec = _decode_funding_rate(rate_raw if isinstance(rate_raw, (bytes, bytearray)) else None)
        bars = _decode_funding_history(hist_raw if isinstance(hist_raw, (bytes, bytearray)) else None)

        history_points: list[FundingHistoryPoint] = []
        hist_rates_list: list[Decimal] = []
        cache_hit = rate_dec is not None and bars is not None

        if bars:
            hist_rates_list = [b.funding_rate for b in bars]
            for bar in bars:
                ts_iso = datetime.fromtimestamp(bar.funding_time_ms / 1000.0, tz=timezone.utc).isoformat()
                history_points.append(
                    FundingHistoryPoint(
                        ts=ts_iso,
                        funding_time_ms=bar.funding_time_ms,
                        rate=str(bar.funding_rate),
                    )
                )

        if rate_dec is None and hist_rates_list:
            rate_dec = hist_rates_list[-1]

        if rate_dec is None:
            rate_dec = Decimal("0")

        annual = annualized_simple_funding_pct(rate_dec)
        long_carry = annual
        short_carry = Decimal("0") - annual

        z_val = calculate_funding_zscore(rate_dec, hist_rates_list if hist_rates_list else [])
        flip_kind, flip_strength, flip_expl = classify_funding_flip(z_val)

        magnitude = float(abs(rate_dec))

        row = FundingCommandRow(
            asset=pair,
            compact_symbol=compact,
            funding_rate_8h=str(rate_dec),
            annualized_simple_pct=str(annual),
            zscore=z_val,
            flip_signal=flip_kind,
            flip_strength=flip_strength,
            flip_explanation=flip_expl,
            effective_long_carry_pct=str(long_carry),
            effective_short_carry_pct=str(short_carry),
            history=history_points,
            cache_hit=cache_hit,
        )
        row_builders.append((magnitude, pair.upper(), row))

    row_builders.sort(key=lambda triple: (-triple[0], triple[1]))
    rows = [triple[2] for triple in row_builders]

    return FundingCommandCenterPayload(
        generated_at=datetime.now(timezone.utc).isoformat(),
        rows=rows,
        dashboard_pairs_used=pairs,
    )
