from __future__ import annotations

import asyncio
from typing import Any, Dict

import asyncpg
from fastapi import APIRouter, Query, Request
from loguru import logger

from atlas.api.agent_vote_history_logic import (
    find_agent_verdict_row,
    infer_vote_correctness,
    trade_direction_from_decision,
)
from atlas.api.decision_journal_logic import parse_jsonb_list

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _merge_verdicts(row: asyncpg.Record) -> list[dict[str, Any]]:
    verdicts = parse_jsonb_list(row.get("agent_verdicts"))
    if verdicts:
        return verdicts
    meta = row.get("metadata")
    if isinstance(meta, dict):
        return parse_jsonb_list(meta.get("agent_verdicts"))
    return []


def _cycle_payload_from_row(row: asyncpg.Record, encoded_agent_name: str) -> Dict[str, Any]:
    verdicts = _merge_verdicts(row)
    verdict = find_agent_verdict_row(verdicts, encoded_agent_name)
    agent_direction = None
    agent_score = 0
    agent_max = 0
    if verdict is not None:
        agent_direction = str(verdict.get("direction", "") or "").strip() or None
        try:
            agent_score = int(verdict.get("score", 0) or 0)
        except (TypeError, ValueError):
            agent_score = 0
        try:
            agent_max = int(verdict.get("max_score", verdict.get("maxScore", 0)) or 0)
        except (TypeError, ValueError):
            agent_max = 0

    decision_raw = str(row["decision"])
    trade_dir = trade_direction_from_decision(decision_raw)
    outcome_raw = row.get("outcome_label")
    outcome_label = str(outcome_raw).strip().upper() if outcome_raw else None
    if outcome_label == "":
        outcome_label = None

    correct = infer_vote_correctness(
        trade_dir=trade_dir,
        agent_dir=agent_direction,
        outcome_label=outcome_label,
    )

    ts = row["created_at"]
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    return {
        "signalId": str(row["signal_id"]),
        "timestamp": ts_str,
        "finalDecision": decision_raw,
        "agentDirection": agent_direction,
        "agentScore": agent_score,
        "agentMaxScore": agent_max,
        "outcomeLabel": outcome_label,
        "correct": correct,
        "totalScore": agent_score,
    }


_AGENT_HISTORY_SQL = """
        SELECT
            sh.signal_id,
            sh.asset,
            sh.created_at,
            sh.decision,
            sh.outcome_label,
            sh.metadata,
            dp.agent_verdicts
        FROM signal_history sh
        LEFT JOIN decision_provenance dp ON dp.signal_id = sh.signal_id
        WHERE UPPER(TRIM(sh.asset)) = $1
        ORDER BY sh.created_at DESC
        LIMIT $2
    """


@router.get("/{encodedName}/history")
async def get_agent_history(
    request: Request,
    encodedName: str,
    asset: str = Query("BTC", min_length=1),
    limit: int = Query(20, ge=1, le=100),
) -> Dict[str, Any]:
    """Last ``limit`` decisions for ``asset`` with this agent's vote and calibration."""
    pool_raw = getattr(request.app.state, "db_pool", None)
    if pool_raw is None:
        return {"asset": asset.strip().upper(), "agent": encodedName, "cycles": []}

    pool: asyncpg.Pool = pool_raw
    asset_norm = asset.strip().upper()

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(_AGENT_HISTORY_SQL, asset_norm, limit)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.error(
            "agent_history_query_failed | agent={} | asset={} | err={}",
            encodedName,
            asset_norm,
            exc,
        )
        return {"asset": asset_norm, "agent": encodedName, "cycles": []}

    cycles = [_cycle_payload_from_row(row, encodedName) for row in rows]
    return {"asset": asset_norm, "agent": encodedName, "cycles": cycles}
