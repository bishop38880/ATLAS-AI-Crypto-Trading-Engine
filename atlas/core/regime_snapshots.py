"""Redis-backed regime history for the Regime Control Center (BTC regime timeline)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import msgspec
from loguru import logger
from redis.asyncio import Redis

UTC = timezone.utc

POLARIS_REGIME_HISTORY_KEY: str = "polaris:regime:history"
POLARIS_REGIME_STATE_KEY: str = "polaris:regime:state"

_HISTORY_MAX_LEN: int = 5000
_HISTORY_TTL_SECONDS: int = 8 * 86_400


def is_btc_signal_asset(asset: str) -> bool:
    """Return True when the signal asset is the primary BTC perpetual / pair."""
    raw = asset.strip().upper().replace("/", "")
    if raw == "BTC":
        return True
    if raw.startswith("BTC") and raw.endswith("USDT"):
        return True
    return False


def _coerce_agent_dict(agent_entry: Any) -> dict[str, Any]:
    if isinstance(agent_entry, dict):
        return agent_entry
    if hasattr(agent_entry, "model_dump"):
        return agent_entry.model_dump(mode="json")  # type: ignore[no-any-return]
    return {}


def extract_regime_agent_payload(signal_dict: dict[str, Any]) -> dict[str, Any]:
    """Return the regime agent sub_signals map from a wire-level signal dict."""
    breakdown = signal_dict.get("agent_breakdown")
    if not isinstance(breakdown, dict):
        return {}

    for key in ("regime",):
        if key not in breakdown:
            continue
        agent_dict = _coerce_agent_dict(breakdown[key])
        sub = agent_dict.get("sub_signals")
        if isinstance(sub, dict):
            return sub
    return {}


def hmm_label_from_signal(signal_dict: dict[str, Any]) -> str | None:
    """Best-effort HMM regime label (bull / bear / volatile) from cached signal JSON."""
    sub = extract_regime_agent_payload(signal_dict)
    raw = sub.get("regime")
    if raw is None:
        return None
    token = str(raw).strip().lower()
    if token in ("bull", "bear", "volatile"):
        return token
    return None


def dashboard_regime_bucket(hmm: str | None) -> str:
    """Match ``coerce_regime_label`` semantics: non-trending HMM states collapse to RANGING."""
    if hmm is None:
        return "UNKNOWN"
    if hmm == "bull":
        return "BULL"
    if hmm == "bear":
        return "BEAR"
    if hmm == "volatile":
        return "RANGING"
    return "UNKNOWN"


async def record_regime_snapshot(
    redis: Redis,
    *,
    signal_dict: dict[str, Any],
    btc_price: str | None,
) -> None:
    """Append a BTC regime snapshot and update dwell state (best-effort, non-blocking)."""
    asset_raw = signal_dict.get("asset")
    if not isinstance(asset_raw, str) or not is_btc_signal_asset(asset_raw):
        return

    hmm = hmm_label_from_signal(signal_dict)
    if hmm is None:
        return

    ts_raw = signal_dict.get("timestamp")
    if isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = datetime.now(tz=UTC)
    elif isinstance(ts_raw, datetime):
        ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=UTC)
    else:
        ts = datetime.now(tz=UTC)
    ts_iso = ts.astimezone(UTC).isoformat()

    sub = extract_regime_agent_payload(signal_dict)
    transition_prob = sub.get("transition_prob")
    duration_bars = sub.get("duration")
    signal_id = str(signal_dict.get("signal_id", ""))

    entry = {
        "ts": ts_iso,
        "asset": asset_raw,
        "hmm_regime": hmm,
        "dashboard_regime": dashboard_regime_bucket(hmm),
        "btc_price": btc_price,
        "transition_prob": float(transition_prob)
        if transition_prob is not None
        else None,
        "duration_bars": int(duration_bars)
        if isinstance(duration_bars, (int, float))
        else None,
        "signal_id": signal_id,
    }

    try:
        pipe = redis.pipeline()
        pipe.lpush(POLARIS_REGIME_HISTORY_KEY, msgspec.json.encode(entry))
        pipe.ltrim(POLARIS_REGIME_HISTORY_KEY, 0, _HISTORY_MAX_LEN - 1)
        pipe.expire(POLARIS_REGIME_HISTORY_KEY, _HISTORY_TTL_SECONDS)

        prev_hmm_raw = await redis.hget(POLARIS_REGIME_STATE_KEY, "hmm_regime")  # type: ignore[union-attr,misc]
        prev_hmm = prev_hmm_raw.decode("utf-8") if isinstance(prev_hmm_raw, (bytes, bytearray)) else (
            str(prev_hmm_raw) if prev_hmm_raw else None
        )

        if prev_hmm != hmm:
            pipe.hset(
                POLARIS_REGIME_STATE_KEY,
                mapping={
                    "hmm_regime": hmm,
                    "since_ts": ts_iso,
                    "previous_hmm": prev_hmm or "",
                    "last_signal_id": signal_id,
                },
            )
        else:
            pipe.hset(
                POLARIS_REGIME_STATE_KEY,
                mapping={
                    "hmm_regime": hmm,
                    "last_signal_id": signal_id,
                },
            )
        pipe.expire(POLARIS_REGIME_STATE_KEY, _HISTORY_TTL_SECONDS)
        await pipe.execute()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("regime_snapshot_record_failed | err={}", str(exc))


async def read_regime_history_window(
    redis: Redis,
    *,
    hours: int = 24 * 7,
) -> list[dict[str, Any]]:
    """Return decoded regime snapshots newest-first, filtered to the UTC window."""
    cutoff = datetime.now(tz=UTC) - timedelta(hours=hours)
    raw_items = await redis.lrange(POLARIS_REGIME_HISTORY_KEY, 0, _HISTORY_MAX_LEN - 1)  # type: ignore[union-attr,misc]
    out: list[dict[str, Any]] = []
    for raw in raw_items:
        if not raw:
            continue
        try:
            row = msgspec.json.decode(raw)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        ts_text = row.get("ts")
        if not isinstance(ts_text, str):
            continue
        try:
            ts = datetime.fromisoformat(ts_text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if ts < cutoff.astimezone(UTC):
            continue
        out.append(row)
    return out


async def read_regime_dwell_state(redis: Redis) -> dict[str, str]:
    """HASH contents describing how long BTC has stayed in the current HMM state."""
    raw = await redis.hgetall(POLARIS_REGIME_STATE_KEY)  # type: ignore[union-attr,misc]
    decoded: dict[str, str] = {}
    for key_b, val_b in raw.items():
        key = key_b.decode("utf-8") if isinstance(key_b, (bytes, bytearray)) else str(key_b)
        val = val_b.decode("utf-8") if isinstance(val_b, (bytes, bytearray)) else str(val_b)
        decoded[key] = val
    return decoded
