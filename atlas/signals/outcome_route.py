"""FastAPI router for Post-Trade Learning feedback loop.

Exposes the endpoint for PROMETHEUS to report trade outcomes, which
are then written to PostgreSQL, analyzed, and published to Redis.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING, cast

from fastapi import APIRouter, Depends, HTTPException
import asyncpg  # type: ignore[import-untyped]
from redis.asyncio import Redis
from loguru import logger
import msgspec

from atlas.signals.outcome import TradeOutcome
from atlas.signals.outcome_classifier import classify_outcome
from atlas.signals.lesson_generator import generate_lesson

if TYPE_CHECKING:
    from atlas.rag.writer import RAGWriter


router = APIRouter(prefix="/api/outcomes", tags=["learning"])


async def get_db_pool() -> asyncpg.Pool:
    """Dependency: PostgreSQL connection pool."""
    from backend.main import app

    pool = app.state.db_pool
    if pool is None:
        raise HTTPException(status_code=503, detail="historical_store_unavailable")
    return pool


async def get_rag_writer() -> "RAGWriter":
    """Dependency: RAG Writer instance."""
    from backend.main import app

    writer = app.state.rag_writer
    if writer is None:
        raise HTTPException(status_code=503, detail="rag_writer_unavailable")
    return writer


async def get_redis() -> Redis:
    """Dependency: Redis client."""
    from backend.main import app

    return cast(Redis, app.state.redis)


async def _fetch_signal_context(pool: asyncpg.Pool, signal_id: str) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT raw_score, decision, reasoning FROM signal_history WHERE signal_id = $1",
        signal_id, timeout=5.0,
    )
    if not row:
        logger.warning("Outcome reported for unknown signal_id={}", signal_id)
        raise HTTPException(status_code=404, detail="Signal not found in history")
    return row


@router.post("/record")
async def record_outcome(
    outcome: TradeOutcome,
    pool: asyncpg.Pool = Depends(get_db_pool),
    writer: "RAGWriter" = Depends(get_rag_writer),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    """Record a trade outcome from PROMETHEUS."""
    row = await _fetch_signal_context(pool, outcome.signal_id)
    raw_score, decision, reasoning = row["raw_score"], row["decision"], row["reasoning"]

    await writer.write_outcome(
        signal_id=outcome.signal_id, pnl_pct=outcome.pnl_pct,
        outcome_label=outcome.market_outcome.value, exit_reason=outcome.exit_reason.value,
    )

    cls = classify_outcome(outcome, raw_score)
    lesson = generate_lesson(outcome, cls, raw_score=raw_score, decision=decision, reasoning=reasoning)

    pattern_id = await writer.write_pattern(
        title=f"Trade Outcome: {outcome.market_outcome.value} ({cls.value})",
        content=lesson, category="post_trade_learning", source="prometheus", relevance_score=1.0,
    )

    payload = {
        "signal_id": outcome.signal_id, "classification": cls.value,
        "pnl_pct": str(outcome.pnl_pct), "pattern_id": pattern_id,
    }
    await redis.publish("atlas:learning_updates", msgspec.json.encode(payload))

    return {
        "status": "success", "signal_id": outcome.signal_id,
        "classification": cls.value, "pattern_id": pattern_id,
    }
