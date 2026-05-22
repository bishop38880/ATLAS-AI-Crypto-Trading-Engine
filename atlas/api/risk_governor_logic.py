"""Build Risk Governor dashboard payloads from Redis."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from prometheus.kill_switch.schemas import SystemHaltEvent

from atlas.api.schemas import (
    PipelineCircuitState,
    RecentRiskVeto,
    RiskGovernorLimits,
    RiskGovernorSnapshot,
    TierExposureRow,
    VetoHistoryBucket,
    VetoHistoryDay,
)
from atlas.core.risk_veto_journal import load_risk_veto_events_raw
from atlas.shared.config import PolarisSettings

_HALT_REDIS_KEY = "prometheus:trading_halted"
_TIER_LABELS: dict[str, str] = {
    "core": "Core",
    "majors": "Majors",
    "l1_l2": "L1 / L2",
    "defi": "DeFi",
    "rotation": "Rotation",
}


def _parse_decimal(raw: bytes | None, default: str = "0") -> Decimal:
    if raw is None:
        return Decimal(default)
    try:
        return Decimal(raw.decode("utf-8"))
    except (InvalidOperation, UnicodeDecodeError):
        return Decimal(default)


def _clamp_pct_fraction(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, x))


async def _read_optional_json_map(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    key: str,
) -> dict[str, Any]:
    raw = await redis_client.get(key)
    if raw is None:
        return {}
    try:
        decoded = msgspec.json.decode(raw)
    except msgspec.DecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _merge_tier_rows(from_redis: dict[str, Any]) -> list[TierExposureRow]:
    rows: list[TierExposureRow] = []
    for tier_id, label in _TIER_LABELS.items():
        raw_val = from_redis.get(tier_id)
        pct = 0.0
        if isinstance(raw_val, (int, float)):
            pct = float(raw_val)
        elif isinstance(raw_val, str):
            try:
                pct = float(raw_val)
            except ValueError:
                pct = 0.0
        # Allow either 0–100 or 0–1 from writers
        if pct > 1.0 + 1e-6:
            pct = pct / 100.0
        rows.append(
            TierExposureRow(
                tier_id=tier_id,
                label=label,
                exposure_pct=_clamp_pct_fraction(pct) * 100.0,
            )
        )
    return rows


async def _circuit_states(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    stages: tuple[str, ...],
) -> list[PipelineCircuitState]:
    out: list[PipelineCircuitState] = []
    for stage in stages:
        raw = await redis_client.get(f"cb:{stage}:state")
        state_s = "unknown"
        if raw is not None:
            try:
                state_s = raw.decode("utf-8").strip().lower()
            except UnicodeError:
                state_s = "unknown"
        out.append(PipelineCircuitState(stage=stage, state=state_s))
    return out


def _aggregate_veto_history(
    events: list[dict[str, Any]],
    *,
    days: int = 30,
) -> tuple[list[VetoHistoryDay], list[VetoHistoryBucket], int, Counter[str]]:
    """Return per-day counts, reason buckets, total in window, reason counter."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).date()
    day_counts: dict[date, int] = {}
    reason_counter: Counter[str] = Counter()
    total_window = 0

    for ev in events:
        ts_raw = ev.get("ts_iso", "")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        d = ts.astimezone(timezone.utc).date()
        if d < start:
            continue
        total_window += 1
        day_counts[d] = day_counts.get(d, 0) + 1
        for r in ev.get("reasons", []) or []:
            reason_counter[str(r)] += 1

    # Build last `days` calendar days (even zeros) for charts
    history_days: list[VetoHistoryDay] = []
    for i in range(days - 1, -1, -1):
        d = (now.date() - timedelta(days=i))
        history_days.append(
            VetoHistoryDay(day_iso=d.isoformat(), veto_count=day_counts.get(d, 0))
        )

    top_buckets: list[VetoHistoryBucket] = []
    for reason, count in reason_counter.most_common(12):
        top_buckets.append(VetoHistoryBucket(reason=reason, count=count))

    return history_days, top_buckets, total_window, reason_counter


async def build_risk_governor_snapshot(
    redis_client: redis_async.Redis,  # type: ignore[type-arg]
    settings: PolarisSettings,
) -> RiskGovernorSnapshot:
    """Hydrate Risk Governor dashboard state from Redis."""
    exposure_frac = float(
        _parse_decimal(await redis_client.get("portfolio:total_exposure_pct"), "0")
    )
    exposure_frac = _clamp_pct_fraction(exposure_frac)

    daily_dd = float(
        _parse_decimal(await redis_client.get("portfolio:daily_drawdown_pct"), "0")
    )
    weekly_dd = float(
        _parse_decimal(await redis_client.get("portfolio:weekly_drawdown_pct"), "0")
    )
    if daily_dd > 1.0:
        daily_dd = daily_dd / 100.0
    if weekly_dd > 1.0:
        weekly_dd = weekly_dd / 100.0

    pnl_1h = float(_parse_decimal(await redis_client.get("portfolio:pnl_1h"), "0"))
    pnl_4h = float(_parse_decimal(await redis_client.get("portfolio:pnl_4h"), "0"))
    pnl_24h = float(_parse_decimal(await redis_client.get("portfolio:pnl_24h"), "0"))

    max_pos_frac = float(
        _parse_decimal(
            await redis_client.get("portfolio:max_single_position_pct"),
            "0.10",
        )
    )
    if max_pos_frac > 1.0:
        max_pos_frac = max_pos_frac / 100.0
    max_pos_frac = _clamp_pct_fraction(max_pos_frac)

    equity = _parse_decimal(await redis_client.get("portfolio:equity_usd"), "0")

    tier_map = await _read_optional_json_map(
        redis_client, "portfolio:exposure_by_tier"
    )
    tier_rows = _merge_tier_rows(tier_map)

    stages = tuple(settings.latency_budgets.keys())
    circuits = await _circuit_states(redis_client, stages)

    trading_halted = bool(await redis_client.exists(_HALT_REDIS_KEY))
    halt_reason: str | None = None
    halt_triggered_by: str | None = None
    halt_ts: str | None = None
    raw_halt = await redis_client.get(_HALT_REDIS_KEY)
    if raw_halt:
        try:
            halt_ev = msgspec.json.decode(raw_halt, type=SystemHaltEvent)
            halt_reason = str(halt_ev.reason)
            halt_triggered_by = halt_ev.triggered_by
            halt_ts = halt_ev.timestamp_iso
        except Exception as exc:
            logger.warning("risk_governor_halt_decode_failed | err={}", str(exc))

    limits = RiskGovernorLimits(
        total_exposure_max_pct=80.0,
        daily_drawdown_limit_pct=3.0,
        weekly_drawdown_limit_pct=7.0,
        intraday_pnl_1h_veto_pct=-3.0,
        intraday_pnl_4h_veto_pct=-5.0,
        intraday_pnl_24h_shutdown_pct=-10.0,
    )

    raw_events = await load_risk_veto_events_raw(redis_client)
    history_days, reason_buckets, vetoes_30d, _counter = _aggregate_veto_history(
        raw_events,
        days=30,
    )

    return RiskGovernorSnapshot(
        as_of_iso=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        total_portfolio_exposure_pct=exposure_frac * 100.0,
        daily_drawdown_pct=daily_dd * 100.0,
        weekly_drawdown_pct=weekly_dd * 100.0,
        trailing_pnl_1h_pct=pnl_1h * 100.0,
        trailing_pnl_4h_pct=pnl_4h * 100.0,
        trailing_pnl_24h_pct=pnl_24h * 100.0,
        max_single_position_allowed_pct=max_pos_frac * 100.0,
        portfolio_equity_usd=str(equity),
        tier_exposure=tier_rows,
        pipeline_circuits=circuits,
        trading_halted=trading_halted,
        halt_reason=halt_reason,
        halt_triggered_by=halt_triggered_by,
        halt_timestamp_iso=halt_ts,
        policy_limits=limits,
        veto_events_30d_total=vetoes_30d,
        veto_history_by_day=history_days,
        veto_reason_buckets=reason_buckets,
        recent_vetoes=[
            RecentRiskVeto(
                ts_iso=str(e.get("ts_iso", "")),
                asset=str(e.get("asset", "")),
                cycle_id=str(e.get("cycle_id", "")),
                reasons=[str(x) for x in (e.get("reasons") or [])],
            )
            for e in raw_events[:40]
        ],
    )
