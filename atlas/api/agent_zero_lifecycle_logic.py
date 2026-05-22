"""Build Agent Zero lifecycle payload for ``GET /api/memory/agent-zero``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import msgspec
from redis.asyncio import Redis

from atlas.shared.config import PolarisSettings

_ESCORE_BANDS: tuple[tuple[str, float, float, str], ...] = (
    ("0.00–0.25", 0.0, 0.25, "ARCHIVED"),
    ("0.25–0.50", 0.25, 0.50, "ARCHIVED"),
    ("0.50–0.75", 0.50, 0.75, "BORDERLINE"),
    ("0.75–1.00", 0.75, 1.01, "RETAINED"),
)

_LAST_LOG_SQL = """
    SELECT run_at, total_scored, archived_count, retained_count,
           avg_escore, error_count
    FROM rag_archive_log
    ORDER BY run_at DESC
    LIMIT 1
"""

_DISTRIBUTION_SQL = """
    SELECT
        SUM(CASE WHEN escore >= 0.0 AND escore < 0.25 THEN 1 ELSE 0 END)::int AS band_0,
        SUM(CASE WHEN escore >= 0.25 AND escore < 0.50 THEN 1 ELSE 0 END)::int AS band_1,
        SUM(CASE WHEN escore >= 0.50 AND escore < 0.75 THEN 1 ELSE 0 END)::int AS band_2,
        SUM(CASE WHEN escore >= 0.75 THEN 1 ELSE 0 END)::int AS band_3,
        AVG(escore)::float AS avg_escore,
        COUNT(*)::int AS scored_total
    FROM signal_history
    WHERE escore IS NOT NULL
"""


async def fetch_agent_zero_lifecycle_payload(
    redis: Redis | None,  # type: ignore[type-arg]
    pool: asyncpg.Pool | None,
    settings: PolarisSettings,
) -> dict[str, Any]:
    """Assemble lifecycle fields from Redis schedule + Postgres archive stats."""
    schedule = await _load_schedule_from_redis(redis)
    last_log = await _load_last_archive_log(pool)
    distribution, avg_escore = await _load_escore_distribution(pool)

    threshold = float(settings.agent_zero_threshold)
    next_iso, next_relative = _resolve_next_run(schedule)
    last_iso, last_relative = _resolve_last_run(schedule, last_log)
    last_results = _build_last_run_results(schedule, last_log)
    health_label = _calculate_collection_health_label(
        distribution_total=sum(b["count"] for b in distribution),
        avg_escore=avg_escore,
        threshold=threshold,
        last_status=_last_run_status(schedule, last_log),
    )

    return {
        "last_run_iso": last_iso,
        "last_run_relative": last_relative,
        "next_run_iso": next_iso,
        "next_run_relative": next_relative,
        "last_run_results": last_results,
        "escore_distribution": distribution,
        "avg_escore": round(avg_escore, 4),
        "threshold": threshold,
        "collection_health_label": health_label,
    }


async def _load_schedule_from_redis(
    redis: Redis | None,  # type: ignore[type-arg]
) -> dict[str, Any] | None:
    if redis is None:
        return None
    raw = await redis.get("polaris:agent_zero:schedule")
    if raw is None:
        return None
    try:
        decoded = msgspec.json.decode(raw)
    except Exception:
        return None
    if isinstance(decoded, dict):
        return decoded
    return None


async def _load_last_archive_log(
    pool: asyncpg.Pool | None,
) -> asyncpg.Record | None:
    if pool is None:
        return None
    try:
        return await pool.fetchrow(_LAST_LOG_SQL, timeout=5.0)
    except Exception:
        return None


async def _load_escore_distribution(
    pool: asyncpg.Pool | None,
) -> tuple[list[dict[str, Any]], float]:
    if pool is None:
        return [], 0.0
    try:
        row = await pool.fetchrow(_DISTRIBUTION_SQL, timeout=5.0)
    except Exception:
        return [], 0.0
    if row is None:
        return [], 0.0

    counts = (
        int(row["band_0"] or 0),
        int(row["band_1"] or 0),
        int(row["band_2"] or 0),
        int(row["band_3"] or 0),
    )
    total = int(row["scored_total"] or 0)
    avg_raw = row["avg_escore"]
    avg_escore = float(avg_raw) if avg_raw is not None else 0.0

    bands: list[dict[str, Any]] = []
    for idx, (label, range_min, range_max, status) in enumerate(_ESCORE_BANDS):
        count = counts[idx]
        percent = (count / total * 100.0) if total > 0 else 0.0
        bands.append(
            {
                "label": label,
                "range_min": range_min,
                "range_max": range_max if range_max <= 1.0 else 1.0,
                "count": count,
                "percent": round(percent, 1),
                "status": status,
            },
        )
    return bands, avg_escore


def _resolve_next_run(schedule: dict[str, Any] | None) -> tuple[str, str]:
    if schedule is None:
        now_iso = datetime.now(timezone.utc).isoformat()
        return now_iso, "Pending (scheduler not published)"
    next_iso = str(schedule.get("nextRunIso") or "")
    next_relative = str(schedule.get("nextRunRelative") or "unknown")
    if not next_iso:
        next_iso = datetime.now(timezone.utc).isoformat()
    return next_iso, next_relative


def _resolve_last_run(
    schedule: dict[str, Any] | None,
    last_log: asyncpg.Record | None,
) -> tuple[str | None, str]:
    last_run = schedule.get("lastRun") if schedule else None
    if isinstance(last_run, dict):
        ts = last_run.get("timestamp")
        if isinstance(ts, str) and ts:
            return ts, _format_relative_past(ts)

    if last_log is not None:
        run_at = last_log["run_at"]
        if isinstance(run_at, datetime):
            iso = run_at.astimezone(timezone.utc).isoformat()
            return iso, _format_relative_past(iso)

    return None, "Never"


def _build_last_run_results(
    schedule: dict[str, Any] | None,
    last_log: asyncpg.Record | None,
) -> dict[str, Any] | None:
    last_run = schedule.get("lastRun") if schedule else None
    if isinstance(last_run, dict):
        scored = last_run.get("documentsScored")
        if scored is not None:
            return {
                "documentsScored": int(scored),
                "archived": int(last_run.get("archived") or 0),
                "retained": int(last_run.get("retained") or 0),
                "errors": int(last_run.get("errors") or 0),
            }

    if last_log is None:
        return None
    return {
        "documentsScored": int(last_log["total_scored"] or 0),
        "archived": int(last_log["archived_count"] or 0),
        "retained": int(last_log["retained_count"] or 0),
        "errors": int(last_log["error_count"] or 0),
    }


def _last_run_status(
    schedule: dict[str, Any] | None,
    last_log: asyncpg.Record | None,
) -> str | None:
    last_run = schedule.get("lastRun") if schedule else None
    if isinstance(last_run, dict):
        status = last_run.get("status")
        if isinstance(status, str):
            return status
    if last_log is not None and int(last_log["error_count"] or 0) > 0:
        return "FAILED"
    if last_log is not None:
        return "PASSED"
    return None


def _calculate_collection_health_label(
    *,
    distribution_total: int,
    avg_escore: float,
    threshold: float,
    last_status: str | None,
) -> str:
    if last_status == "FAILED":
        return "DEGRADED — last run errors"
    if distribution_total == 0:
        return "AWAITING DATA"
    if avg_escore >= threshold:
        return "HEALTHY"
    if avg_escore >= threshold * 0.85:
        return "STABLE"
    return "NEEDS REVIEW"


def _format_relative_past(iso_timestamp: str) -> str:
    try:
        parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return iso_timestamp
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86_400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86_400}d ago"
