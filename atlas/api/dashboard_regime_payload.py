"""Regime Control Center REST payload — aggregates Redis signal + dwell + history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field
from redis.asyncio import Redis

from atlas.api.routes.signals import _gate_threshold_value, _redis_get_signal_dict
from atlas.api.schemas import _BaseConfig
from atlas.core.regime_snapshots import (
    dashboard_regime_bucket,
    extract_regime_agent_payload,
    hmm_label_from_signal,
    read_regime_dwell_state,
    read_regime_history_window,
)
from atlas.ml.regime_weights import REGIME_WEIGHTS
from atlas.shared.config import PolarisSettings

UTC = timezone.utc


def _parse_iso_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _format_dwell(since: datetime, now: datetime) -> str:
    seconds = max(0, int((now - since.astimezone(UTC)).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        rem_h = seconds % 3600
        return f"{seconds // 3600}h {rem_h // 60}m"
    rem_d = seconds % 86400
    return f"{seconds // 86400}d {rem_d // 3600}h"


def _sorted_history_asc(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: str(r.get("ts", "")))


def _count_switches(rows_asc: list[dict[str, Any]]) -> int:
    if len(rows_asc) < 2:
        return 0
    total = 0
    prev = str(rows_asc[0].get("hmm_regime", ""))
    for row in rows_asc[1:]:
        cur = str(row.get("hmm_regime", ""))
        if cur != prev:
            total += 1
        prev = cur
    return total


def _timeline_segments(rows_asc: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows_asc:
        return []
    now = datetime.now(tz=UTC)
    segments: list[dict[str, Any]] = []
    for idx, row in enumerate(rows_asc):
        start_ts = _parse_iso_ts(str(row.get("ts", ""))) if row.get("ts") else None
        if start_ts is None:
            continue
        if idx + 1 < len(rows_asc):
            end_ts = _parse_iso_ts(str(rows_asc[idx + 1].get("ts", "")))
            if end_ts is None:
                continue
        else:
            end_ts = now
        segments.append({
            "start_ts": start_ts.astimezone(UTC).isoformat(),
            "end_ts": end_ts.astimezone(UTC).isoformat(),
            "hmm_regime": str(row.get("hmm_regime", "")),
            "dashboard_regime": str(row.get("dashboard_regime", "")),
            "start_price_usd": row.get("btc_price"),
        })
    return segments


def _coerce_agent_regime_block(signal_dict: dict[str, Any]) -> dict[str, Any]:
    breakdown = signal_dict.get("agent_breakdown")
    if not isinstance(breakdown, dict):
        return {}
    raw = breakdown.get("regime")
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump(mode="json")  # type: ignore[no-any-return]
    return {}


def _classification_rows(
    signal_dict: dict[str, Any],
    sub_signals: dict[str, Any],
    agent_block: dict[str, Any],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    probs: dict[str, Any] | None = None
    raw_probs = sub_signals.get("probabilities")
    if isinstance(raw_probs, dict):
        probs = raw_probs
    if probs:
        ranked = sorted(
            ((str(k), float(v)) for k, v in probs.items()),
            key=lambda kv: kv[1],
            reverse=True,
        )[:3]
        for name, val in ranked:
            rows.append({
                "label": f"HMM posterior · {name}",
                "detail": f"{val * 100.0:.1f}%",
            })
    tr = sub_signals.get("transition_prob")
    if tr is not None:
        try:
            rows.append({
                "label": "Transition risk (next bar)",
                "detail": f"{float(tr) * 100.0:.1f}%",
            })
        except (TypeError, ValueError):
            pass
    dur = sub_signals.get("duration")
    if dur is not None:
        rows.append({
            "label": "Regime duration (30m bars)",
            "detail": str(int(dur)) if isinstance(dur, (int, float)) else str(dur),
        })
    expl = str(agent_block.get("explanation", "")).strip()
    if expl:
        rows.append({"label": "Regime agent", "detail": expl[:280]})
    conf_dims = signal_dict.get("confidence_dimensions")
    if isinstance(conf_dims, dict):
        rs = conf_dims.get("regime_stability")
        if rs is not None:
            try:
                rs_f = float(rs)
            except (TypeError, ValueError):
                rs_f = None
            if rs_f is not None:
                rows.append({
                    "label": "Pipeline confidence · regime stability",
                    "detail": f"{rs_f:.2f} on [0,1] from pipeline diagnostics",
                })
    return rows[:8]


def _adjustment_rows(
    hmm: str | None,
    signal_dict: dict[str, Any],
    settings: PolarisSettings,
) -> list[dict[str, str]]:
    gate = int(round(float(_gate_threshold_value(signal_dict, settings))))
    base_gate = int(round(float(settings.router_gated_threshold)))
    mandatory = int(round(float(settings.router_mandatory_threshold)))

    tier_o = signal_dict.get("confidence_tier") or signal_dict.get("confidenceTier")
    tier = str(tier_o) if tier_o is not None else "UNKNOWN"

    mod_raw = signal_dict.get("position_size_modifier")
    if mod_raw is None:
        mod_raw = signal_dict.get("positionSizeModifier")
    try:
        mod = float(mod_raw) if mod_raw is not None else 1.0
    except (TypeError, ValueError):
        mod = 1.0

    weights = REGIME_WEIGHTS.get(hmm or "volatile", REGIME_WEIGHTS["volatile"])
    focus = sorted(
        weights.items(),
        key=lambda kv: abs(1.0 - kv[1]),
        reverse=True,
    )[:4]

    lines: list[dict[str, str]] = [
        {
            "label": "Conviction gate (ladder)",
            "value": f"≥ {gate} pts",
            "detail": (
                f"Baseline router gate {base_gate} / mandatory {mandatory}. "
                f"{'Signal overrides gate.' if gate != base_gate else 'Using baseline gate.'}"
            ),
        },
        {
            "label": "Position-size modifier",
            "value": f"{mod:.2f}×",
            "detail": "From pipeline confidence tier (STANDARD=1.0, REDUCED≈0.5, SKIP→no trade).",
        },
        {
            "label": f"Regime-blended pillar emphasis ({hmm or 'unknown'})",
            "value": "see table",
            "detail": "; ".join(f"{k}×{v:.2f}" for k, v in focus),
        },
    ]
    return lines


class RegimeTimelineSegment(BaseModel):
    model_config = _BaseConfig

    start_ts: str
    end_ts: str
    hmm_regime: str
    dashboard_regime: str
    start_price_usd: str | None = None


class RegimeControlCenterPayload(BaseModel):
    model_config = _BaseConfig

    as_of: str = Field(description="UTC timestamp when this view was assembled.")
    focus_asset: str
    signal_asset: str
    hmm_regime: str | None
    dashboard_regime: str
    regime_confidence: float
    since_utc: str | None
    dwell_label: str
    dwell_started_at: str | None
    gate_threshold: int
    position_size_modifier: float
    confidence_tier: str
    regime_probabilities: dict[str, float]
    classification_rows: list[dict[str, str]]
    adjustment_rows: list[dict[str, str]]
    timeline: list[RegimeTimelineSegment]
    regime_switches_7d: int
    history_samples_7d: int
    note_dashboard_ranging: str


async def build_regime_control_center_payload(
    redis: Redis,
    settings: PolarisSettings,
    *,
    focus_asset: str = "BTC",
) -> RegimeControlCenterPayload:
    """Compose the Regime Control Center JSON for the operator dashboard."""
    signal_dict = await _redis_get_signal_dict(redis, focus_asset)
    now = datetime.now(tz=UTC)

    hmm = hmm_label_from_signal(signal_dict)
    display = dashboard_regime_bucket(hmm)
    sub = extract_regime_agent_payload(signal_dict)
    agent_block = _coerce_agent_regime_block(signal_dict)

    raw_probs = sub.get("probabilities") if isinstance(sub, dict) else None
    probs: dict[str, float] = {}
    if isinstance(raw_probs, dict):
        for k, v in raw_probs.items():
            try:
                probs[str(k)] = float(v)
            except (TypeError, ValueError):
                continue

    conf_raw = signal_dict.get("confidence")
    try:
        conf = float(conf_raw) if conf_raw is not None else 0.0
    except (TypeError, ValueError):
        conf = 0.0

    dwell = await read_regime_dwell_state(redis)
    since_txt = dwell.get("since_ts") or dwell.get("sinceTs")
    parsed_since = _parse_iso_ts(since_txt if isinstance(since_txt, str) else None)
    if parsed_since is None and signal_dict.get("timestamp"):
        parsed_since = _parse_iso_ts(str(signal_dict.get("timestamp")))

    dwell_label = "unknown"
    if parsed_since is not None:
        dwell_label = _format_dwell(parsed_since, now)

    tier_o = signal_dict.get("confidence_tier") or signal_dict.get("confidenceTier")
    tier_s = str(tier_o) if tier_o is not None else "UNKNOWN"

    mod_raw = signal_dict.get("position_size_modifier") or signal_dict.get("positionSizeModifier")
    try:
        mod = float(mod_raw) if mod_raw is not None else 1.0
    except (TypeError, ValueError):
        mod = 1.0

    gate = int(round(float(_gate_threshold_value(signal_dict, settings))))

    history = await read_regime_history_window(redis, hours=24 * 7)
    rows_asc = _sorted_history_asc(history)
    switches = _count_switches(rows_asc)
    seg_dicts = _timeline_segments(rows_asc)
    segments = [
        RegimeTimelineSegment(
            start_ts=sd["start_ts"],
            end_ts=sd["end_ts"],
            hmm_regime=sd["hmm_regime"],
            dashboard_regime=sd["dashboard_regime"],
            start_price_usd=str(sd["start_price_usd"]) if sd.get("start_price_usd") else None,
        )
        for sd in seg_dicts
    ]

    note = (
        "POLARIS dashboard bucket maps HMM `volatile` and other non-trending states into RANGING — "
        "this page surfaces the underlying HMM label to avoid silent misreads."
    )

    return RegimeControlCenterPayload(
        as_of=now.isoformat(),
        focus_asset=str(focus_asset).upper(),
        signal_asset=str(signal_dict.get("asset", "")) if signal_dict.get("asset") else "",
        hmm_regime=hmm,
        dashboard_regime=display,
        regime_confidence=conf,
        since_utc=parsed_since.astimezone(UTC).isoformat() if parsed_since else None,
        dwell_label=dwell_label,
        dwell_started_at=parsed_since.astimezone(UTC).isoformat() if parsed_since else None,
        gate_threshold=gate,
        position_size_modifier=mod,
        confidence_tier=tier_s,
        regime_probabilities=probs,
        classification_rows=_classification_rows(signal_dict, sub, agent_block),
        adjustment_rows=_adjustment_rows(hmm, signal_dict, settings),
        timeline=segments,
        regime_switches_7d=switches,
        history_samples_7d=len(history),
        note_dashboard_ranging=note,
    )
