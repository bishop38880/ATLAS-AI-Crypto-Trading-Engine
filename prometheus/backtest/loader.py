"""Helpers to load candles and signals via ``BacktestDB``."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import msgspec
import polars as pl

from prometheus.backtest.config import CandleRow, SignalRow
from prometheus.backtest.db import BacktestDB


async def load_candles_window(
    db: BacktestDB,
    asset: str,
    timeframe: str,
    start_ts: int,
    end_ts: int,
) -> list[CandleRow]:
    """Fetch candles ordered by timestamp ascending."""
    rows = await db.fetch_candles(asset, timeframe, start_ts, end_ts)
    return sorted(rows, key=lambda r: r.ts)


async def load_signals_window(
    db: BacktestDB,
    asset: str,
    start_ts: int,
    end_ts: int,
) -> list[SignalRow]:
    """Fetch signals ordered by timestamp ascending."""
    rows = await db.fetch_signals(asset, start_ts, end_ts)
    return sorted(rows, key=lambda r: r.ts)


def _signal_row_from_payload(payload: dict[str, object], raw_json: str) -> SignalRow:
    signal_id = str(payload.get("signal_id", ""))
    asset = str(payload.get("asset", ""))
    ts_ms = extract_ts_ms_from_payload(payload)

    dir_override = payload.get("direction")
    act_override = payload.get("action")
    if isinstance(dir_override, str) and isinstance(act_override, str):
        direction, action = dir_override, act_override
    else:
        decision = str(payload.get("decision", ""))
        action_obj = payload.get("action")
        direction, action = map_decision_to_direction_action(decision, action_obj)

    score_raw = payload.get("total_score", payload.get("score", 0))
    total_score = Decimal(str(score_raw))
    confidence = Decimal(str(payload.get("confidence", "0")))

    risk_veto = bool(payload.get("risk_veto", False))
    br = payload.get("agent_breakdown")
    if not risk_veto and isinstance(br, dict):
        risk_veto = risk_agent_veto_from_breakdown(br)

    ttl_seconds = ttl_seconds_from_payload(payload)

    return SignalRow(
        signal_id=signal_id,
        asset=asset,
        ts=ts_ms,
        direction=direction,
        action=action,
        total_score=total_score,
        confidence=confidence,
        risk_veto=risk_veto,
        ttl_seconds=ttl_seconds,
        raw_json=raw_json,
    )


def signal_payload_to_row(raw_bytes: bytes) -> SignalRow:
    """Map ATLAS ``SignalOutput`` JSON object bytes to ``SignalRow``."""
    payload = msgspec.json.decode(raw_bytes)
    if not isinstance(payload, dict):
        raise ValueError("signal record must be a JSON object")
    return _signal_row_from_payload(payload, raw_bytes.decode("utf-8"))


def extract_ts_ms_from_payload(payload: dict[str, object]) -> int:
    """Resolve unix milliseconds from ``timestamp``."""
    ts_val = payload.get("timestamp")
    if isinstance(ts_val, (int, float)):
        return int(ts_val)
    if isinstance(ts_val, str):
        dt = datetime.fromisoformat(ts_val.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    raise ValueError("timestamp must be int ms or ISO string in replay payload")


def map_decision_to_direction_action(
    decision: str,
    action_obj: object,
) -> tuple[str, str]:
    """Translate SignalDecision + optional ActionBlock into replay enums."""
    actionable = isinstance(action_obj, dict)
    if decision in ("Strong Buy", "Buy") and actionable:
        return "LONG", "OPEN"
    if decision in ("Strong Sell", "Sell") and actionable:
        return "SHORT", "OPEN"
    if decision == "No Position":
        return "NO_POSITION", "CLOSE"
    if decision == "Hold":
        return "NO_POSITION", "HOLD"
    return "NO_POSITION", "HOLD"


def risk_agent_veto_from_breakdown(breakdown: dict[str, object]) -> bool:
    """Detect risk agent veto inside ``agent_breakdown``."""
    risk = breakdown.get("risk")
    if not isinstance(risk, dict):
        return False
    return bool(risk.get("veto", False))


def ttl_seconds_from_payload(payload: dict[str, object]) -> int | None:
    """TTL seconds from ``expires_at`` minus ``timestamp``."""
    exp_raw = payload.get("expires_at")
    ts_raw = payload.get("timestamp")
    if not isinstance(exp_raw, str) or not isinstance(ts_raw, str):
        return None
    exp_dt = datetime.fromisoformat(exp_raw.replace("Z", "+00:00"))
    ts_dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
    delta = exp_dt - ts_dt
    return max(int(delta.total_seconds()), 0)


async def import_candles_from_csv(
    db: BacktestDB,
    csv_path: str | Path,
    asset: str,
    timeframe: str,
) -> int:
    """Parse OHLCV CSV (ts,open,high,low,close,volume) and bulk-insert."""
    path = Path(csv_path)
    df = pl.read_csv(
        path,
        schema_overrides={
            "ts": pl.Int64,
            "open": pl.Utf8,
            "high": pl.Utf8,
            "low": pl.Utf8,
            "close": pl.Utf8,
            "volume": pl.Utf8,
        },
    )
    rows: list[CandleRow] = []
    for row in df.iter_rows(named=True):
        rows.append(
            CandleRow(
                asset=asset,
                timeframe=timeframe,
                ts=int(row["ts"]),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            ),
        )
    await db.insert_candles(rows)
    return len(rows)
