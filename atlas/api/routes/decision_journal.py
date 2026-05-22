"""GET /api/decisions/journal — queryable decision log for the Trade Journal UI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg
from fastapi import HTTPException, Query, Request, status
from loguru import logger

from atlas.api.decision_journal_logic import (
    build_score_breakdown_label,
    outcome_horizons_from_metadata,
    parse_jsonb_list,
    resolve_primary_agent,
)
from atlas.api.schemas import (
    AgentVerdictJournal,
    DecisionJournalEntry,
    DecisionJournalResponse,
    GateEventJournal,
    OutcomeHorizonsPayload,
    RiskManagerJournal,
)


_SINCE_MAP = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def _metadata_dict(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    return {}


def _infer_action_taken(
    decision: str,
    risk_vetoed: bool,
    metadata: dict[str, Any],
) -> str:
    explicit = metadata.get("action_taken") or metadata.get("actionTaken")
    if explicit in ("executed", "skipped", "rejected"):
        return str(explicit)
    if risk_vetoed:
        return "rejected"
    d = decision.strip().lower()
    if d in ("hold", "no position"):
        return "skipped"
    return "executed"


def _risk_from_verdicts(agent_verdicts: list[dict[str, Any]]) -> RiskManagerJournal:
    risk_rows = [
        v for v in agent_verdicts if str(v.get("agent_name", "")).lower() == "risk"
    ]
    vetoed = any(bool(v.get("veto")) for v in risk_rows)
    reason = ""
    if risk_rows:
        pick = next((v for v in risk_rows if v.get("veto")), risk_rows[0])
        reason = str(pick.get("explanation") or pick.get("direction") or "")
    return RiskManagerJournal(
        passed=not vetoed,
        vetoed=vetoed,
        reason=reason or ("Risk Manager veto" if vetoed else "Risk Manager cleared"),
    )


def _gates_from_metadata(
    metadata: dict[str, Any],
    pipeline_confidence: float | None,
) -> list[GateEventJournal]:
    raw = metadata.get("gates_fired") or metadata.get("gatesFired")
    out: list[GateEventJournal] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                out.append(
                    GateEventJournal(
                        name=str(item.get("name", "gate")),
                        fired=bool(item.get("fired", True)),
                        detail=str(item.get("detail", "")),
                    )
                )
            elif isinstance(item, str):
                out.append(GateEventJournal(name=item, fired=True, detail=""))
    conf_gate = metadata.get("conviction_gate_passed")
    if isinstance(conf_gate, bool):
        out.append(
            GateEventJournal(
                name="conviction_gate",
                fired=not conf_gate,
                detail="position_size_modifier at 0" if not conf_gate else "passed",
            )
        )
    if pipeline_confidence is not None and pipeline_confidence < 0.35:
        out.append(
            GateEventJournal(
                name="pipeline_confidence",
                fired=True,
                detail=f"low pipeline confidence ({pipeline_confidence:.2f})",
            )
        )
    return out


def _entry_price_from_metadata(metadata: dict[str, Any]) -> str:
    p = metadata.get("entry_price") or metadata.get("entryPrice") or metadata.get("suggested_entry")
    if p is not None and str(p).strip():
        return str(p)
    action = metadata.get("action")
    if isinstance(action, dict) and action.get("price") is not None:
        return str(action["price"])
    return "—"


def _row_to_entry(
    row: asyncpg.Record,
    *,
    merge_verdicts: list[dict[str, Any]] | None,
) -> DecisionJournalEntry:
    meta = _metadata_dict(row["metadata"])
    verdicts_raw: Any = merge_verdicts if merge_verdicts is not None else row.get("agent_verdicts")
    parsed = parse_jsonb_list(verdicts_raw)
    if not parsed and isinstance(meta.get("agent_verdicts"), list):
        parsed = parse_jsonb_list(meta.get("agent_verdicts"))

    pipeline_conf: float | None = None
    raw_pc = row.get("pipeline_confidence")
    if raw_pc is not None:
        try:
            pipeline_conf = float(raw_pc)
        except (TypeError, ValueError):
            pipeline_conf = None

    tier = row.get("confidence_tier")
    tier_s = str(tier) if tier is not None else None

    risk = _risk_from_verdicts(parsed)
    if isinstance(meta.get("risk_manager"), dict):
        rm = meta["risk_manager"]
        risk = RiskManagerJournal(
            passed=bool(rm.get("passed", not rm.get("vetoed"))),
            vetoed=bool(rm.get("vetoed")),
            reason=str(rm.get("reason", risk.reason)),
        )

    gates = _gates_from_metadata(meta, pipeline_conf)
    primary = resolve_primary_agent(parsed, meta)
    pnl = row.get("pnl_pct")
    pnl_f = float(pnl) if pnl is not None else None

    h1, h4, h24 = outcome_horizons_from_metadata(meta, pnl_f)
    ts = row["created_at"]
    ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

    score = int(row["score"])
    raw_score = int(row["raw_score"])
    conf = float(row["confidence"])
    decision = str(row["decision"])

    return DecisionJournalEntry(
        signal_id=str(row["signal_id"]),
        asset=str(row["asset"]),
        timestamp=ts_str,
        timeframe=str(row.get("timeframe") or "30m"),
        score_breakdown=build_score_breakdown_label(score, raw_score, conf),
        normalized_score=score,
        raw_score=raw_score,
        decision=decision,
        confidence=conf,
        action_taken=_infer_action_taken(decision, risk.vetoed, meta),
        entry_price=_entry_price_from_metadata(meta),
        outcome_pct=pnl_f,
        outcome_horizons=OutcomeHorizonsPayload(pct_1h=h1, pct_4h=h4, pct_24h=h24),
        reasoning_summary=str(row.get("reasoning") or ""),
        primary_agent=primary,
        agent_verdicts=[
            AgentVerdictJournal(
                agent_name=str(v.get("agent_name", v.get("agentName", "?"))),
                state=str(v.get("state", "READY")),
                score=int(v.get("score", 0) or 0),
                max_score=int(v.get("max_score", v.get("maxScore", 0)) or 0),
                direction=str(v.get("direction", "neutral")),
                veto=bool(v.get("veto", False)),
            )
            for v in parsed
        ],
        gates=gates,
        risk_manager=risk,
        pipeline_confidence=pipeline_conf,
        confidence_tier=tier_s,
        exit_reason=str(row["exit_reason"]) if row.get("exit_reason") else None,
    )


async def _fetch_journal_with_provenance(
    conn: asyncpg.Connection,
    *,
    cutoff: datetime | None,
    score_min: int,
    score_max: int,
    outcome: str,
    agent: str,
    limit: int,
    offset: int,
) -> tuple[list[asyncpg.Record], int]:
    """Return rows plus total count; outcome and score enforced in SQL."""
    conditions: list[str] = ["TRUE"]
    args: list[Any] = []
    idx = 0

    if cutoff is not None:
        idx += 1
        conditions.append(f"sh.created_at >= ${idx}")
        args.append(cutoff)

    idx += 1
    conditions.append(f"sh.score >= ${idx}")
    args.append(score_min)

    idx += 1
    conditions.append(f"sh.score <= ${idx}")
    args.append(score_max)

    if outcome == "pending":
        conditions.append("sh.pnl_pct IS NULL AND sh.outcome_label IS NULL")
    elif outcome == "win":
        conditions.append("sh.outcome_label = 'WIN'")
    elif outcome == "loss":
        conditions.append("sh.outcome_label = 'LOSS'")
    elif outcome == "scratch":
        conditions.append("sh.outcome_label = 'SCRATCH'")

    agent_clause = ""
    if agent.strip():
        idx += 1
        agent_clause = (
            f" AND ( EXISTS (SELECT 1 FROM jsonb_array_elements("
            f"COALESCE(dp.agent_verdicts, '[]'::jsonb)) e "
            f"WHERE e->>'agent_name' ILIKE '%' || ${idx} || '%') "
            f" OR (sh.metadata->>'primary_agent') ILIKE '%' || ${idx} || '%' "
            f" OR EXISTS (SELECT 1 FROM jsonb_array_elements("
            f"COALESCE(sh.metadata->'agent_verdicts', '[]'::jsonb)) m "
            f"WHERE m->>'agent_name' ILIKE '%' || ${idx} || '%') )"
        )
        args.append(agent.strip())

    where_sql = " AND ".join(conditions) + agent_clause

    count_q = f"SELECT COUNT(*) FROM signal_history sh LEFT JOIN decision_provenance dp ON dp.signal_id = sh.signal_id WHERE {where_sql}"
    list_q = f"""
        SELECT
            sh.signal_id,
            sh.asset,
            sh.created_at,
            sh.timeframe,
            sh.decision,
            sh.score,
            sh.raw_score,
            sh.confidence,
            sh.reasoning,
            sh.pnl_pct,
            sh.outcome_label,
            sh.metadata,
            sh.exit_reason,
            dp.agent_verdicts,
            dp.pipeline_confidence,
            dp.confidence_tier
        FROM signal_history sh
        LEFT JOIN decision_provenance dp ON dp.signal_id = sh.signal_id
        WHERE {where_sql}
        ORDER BY sh.created_at DESC
        LIMIT {limit + 1} OFFSET {offset}
    """

    total = int(await conn.fetchval(count_q, *args))
    rows = await conn.fetch(list_q, *args)
    return rows, total


async def _fetch_journal_signal_history_only(
    conn: asyncpg.Connection,
    *,
    cutoff: datetime | None,
    score_min: int,
    score_max: int,
    outcome: str,
    agent: str,
    limit: int,
    offset: int,
) -> tuple[list[asyncpg.Record], int]:
    """Fallback without decision_provenance table."""
    conditions: list[str] = ["TRUE"]
    args: list[Any] = []
    idx = 0

    if cutoff is not None:
        idx += 1
        conditions.append(f"sh.created_at >= ${idx}")
        args.append(cutoff)

    idx += 1
    conditions.append(f"sh.score >= ${idx}")
    args.append(score_min)

    idx += 1
    conditions.append(f"sh.score <= ${idx}")
    args.append(score_max)

    if outcome == "pending":
        conditions.append("sh.pnl_pct IS NULL AND sh.outcome_label IS NULL")
    elif outcome == "win":
        conditions.append("sh.outcome_label = 'WIN'")
    elif outcome == "loss":
        conditions.append("sh.outcome_label = 'LOSS'")
    elif outcome == "scratch":
        conditions.append("sh.outcome_label = 'SCRATCH'")

    agent_clause = ""
    if agent.strip():
        idx += 1
        agent_clause = (
            f" AND ( (sh.metadata->>'primary_agent') ILIKE '%' || ${idx} || '%' "
            f" OR EXISTS (SELECT 1 FROM jsonb_array_elements("
            f"COALESCE(sh.metadata->'agent_verdicts', '[]'::jsonb)) m "
            f"WHERE m->>'agent_name' ILIKE '%' || ${idx} || '%') )"
        )
        args.append(agent.strip())

    where_sql = " AND ".join(conditions) + agent_clause

    count_q = f"SELECT COUNT(*) FROM signal_history sh WHERE {where_sql}"
    list_q = f"""
        SELECT
            sh.signal_id,
            sh.asset,
            sh.created_at,
            sh.timeframe,
            sh.decision,
            sh.score,
            sh.raw_score,
            sh.confidence,
            sh.reasoning,
            sh.pnl_pct,
            sh.outcome_label,
            sh.metadata,
            sh.exit_reason,
            NULL::jsonb AS agent_verdicts,
            NULL::float AS pipeline_confidence,
            NULL::text AS confidence_tier
        FROM signal_history sh
        WHERE {where_sql}
        ORDER BY sh.created_at DESC
        LIMIT {limit + 1} OFFSET {offset}
    """

    total = int(await conn.fetchval(count_q, *args))
    rows = await conn.fetch(list_q, *args)
    return rows, total


async def get_decision_journal(
    request: Request,
    limit: int = Query(40, ge=1, le=200),
    offset: int = Query(0, ge=0),
    since: str = Query("30d"),
    outcome: str = Query("all"),
    score_min: int = Query(0, ge=0, le=100),
    score_max: int = Query(100, ge=0, le=100),
    agent: str = Query(""),
) -> DecisionJournalResponse:
    pool_raw = request.app.state.db_pool
    if pool_raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="historical_store_unavailable",
        )
    pool: asyncpg.Pool = pool_raw

    delta = _SINCE_MAP.get(since, timedelta(days=30))
    cutoff = datetime.now(timezone.utc) - delta

    outcome_key = outcome.strip().lower()
    if outcome_key not in ("all", "pending", "win", "loss", "scratch"):
        outcome_key = "all"

    async with pool.acquire() as conn:
        try:
            rows, total = await _fetch_journal_with_provenance(
                conn,
                cutoff=cutoff,
                score_min=score_min,
                score_max=score_max,
                outcome=outcome_key,
                agent=agent,
                limit=limit,
                offset=offset,
            )
        except Exception as exc:
            logger.warning(
                "decision_journal_provenance_query_failed | err={} | fallback=signal_history_only",
                str(exc),
            )
            rows, total = await _fetch_journal_signal_history_only(
                conn,
                cutoff=cutoff,
                score_min=score_min,
                score_max=score_max,
                outcome=outcome_key,
                agent=agent,
                limit=limit,
                offset=offset,
            )

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    entries: list[DecisionJournalEntry] = []
    for rec in page_rows:
        merge_verdicts: list[dict[str, Any]] | None = None
        sh_meta = _metadata_dict(rec["metadata"])
        dp_verdicts = rec.get("agent_verdicts")
        meta_verdicts = sh_meta.get("agent_verdicts")
        if dp_verdicts:
            merge_verdicts = parse_jsonb_list(dp_verdicts)
        elif isinstance(meta_verdicts, list):
            merge_verdicts = parse_jsonb_list(meta_verdicts)
        entry = _row_to_entry(rec, merge_verdicts=merge_verdicts)
        entries.append(entry)

    next_off = offset + limit if has_more else None
    return DecisionJournalResponse(
        entries=entries,
        total=total,
        has_more=has_more,
        next_offset=next_off,
    )
