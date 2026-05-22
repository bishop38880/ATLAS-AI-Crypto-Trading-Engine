"""Analysis Log API — paginated query for signal analysis records.

Endpoint:
    GET /api/log/analyses?limit=100&offset=0&group=&search=&since=24h&asset=

Returns AnalysisLogResponse shape expected by AnalysisLogPage.tsx.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from fastapi import APIRouter, Query, Request
from loguru import logger

router = APIRouter(prefix="/api/log", tags=["log"])

SINCE_MAP = {
    "1h": timedelta(hours=1),
    "6h": timedelta(hours=6),
    "12h": timedelta(hours=12),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@router.get("/analyses")
async def get_analyses(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    group: str = Query(""),
    search: str = Query(""),
    since: str = Query("24h"),
    asset: Optional[str] = None,
) -> Dict[str, Any]:
    """Return paginated analysis records from Postgres signals table."""
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return {
            "analyses": [],
            "total": 0,
            "has_more": False,
            "next_offset": None,
        }

    # Build time filter
    delta = SINCE_MAP.get(since, timedelta(hours=24))
    cutoff = datetime.now(UTC) - delta

    # Build query
    conditions = ["timestamp >= $1"]
    args: list[Any] = [cutoff]
    idx = 2

    if asset:
        conditions.append(f"asset = ${idx}")
        args.append(asset)
        idx += 1

    if search:
        conditions.append(f"(asset ILIKE ${idx} OR decision ILIKE ${idx})")
        args.append(f"%{search}%")
        idx += 1

    where = " AND ".join(conditions)

    # Get total count
    count_query = f"SELECT COUNT(*) FROM signals WHERE {where}"
    data_query = f"""
        SELECT id, asset, timestamp, total_score, decision, conviction,
               passes_gate, confidence
        FROM signals
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT ${idx} OFFSET ${idx + 1}
    """
    args_data = [*args, limit, offset]

    try:
        async with pool.acquire() as conn:
            total = await conn.fetchval(count_query, *args)
            rows = await conn.fetch(data_query, *args_data)
    except Exception as e:
        logger.error("analysis_log_query_failed | error={}", str(e))
        return {
            "analyses": [],
            "total": 0,
            "has_more": False,
            "next_offset": None,
        }

    analyses = []
    for row in rows:
        ts = row["timestamp"]
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        score = float(row["total_score"])
        decision_raw = row["decision"]
        conviction = float(row["conviction"])
        passes = bool(row["passes_gate"])
        confidence_val = float(row.get("confidence", conviction))

        # Map to group classification
        if passes and decision_raw in ("LONG", "SHORT", "Strong Buy", "Buy", "Sell", "Strong Sell"):
            grp = "actioned"
        elif score >= 100:
            grp = "watching"
        else:
            grp = "suppressed"

        # Apply group filter
        if group and group != "all" and grp != group:
            continue

        # Map decision to display format
        decision_map = {
            "LONG": "Strong Buy", "SHORT": "Strong Sell",
            "FLAT": "Hold", "REJECT": "No Position",
        }
        display_decision = decision_map.get(decision_raw, decision_raw)

        analyses.append({
            "signalId": str(row["id"]),
            "asset": row["asset"],
            "cycleTimestamp": ts_str,
            "cycleNumber": 0,
            "decision": display_decision,
            "group": grp,
            "emitToPrometheus": passes,
            "confluenceScore": score,
            "normalizedScore": score,
            "confidence": confidence_val,
            "riskLevel": "low" if score < 100 else "medium" if score < 140 else "high",
            "categoryScores": {
                "derivatives": 0, "onchain": 0, "technical": 0,
                "sentiment": 0, "marketContext": 0,
            },
            "reasoningSummary": f"{row['asset']} analysis — score {score:.0f}",
            "keyConvergences": [],
            "keyRisks": [],
            "suggestedNextActions": [],
            "modelUsed": "deepseek-chat",
            "llmTier": "fast",
            "escalationReason": None,
            "agentResults": [],
            "priceAtCycle": "0",
            "fundingRateAtCycle": "0",
            "obtiSummaryAtCycle": None,
            "action": None,
            "outcome": None,
        })

    has_more = (offset + limit) < total
    next_off = offset + limit if has_more else None

    return {
        "analyses": analyses,
        "total": total,
        "has_more": has_more,
        "next_offset": next_off,
    }
