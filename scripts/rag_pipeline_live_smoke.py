#!/usr/bin/env python3
"""End-to-end smoke test for ``RAGPipeline`` against live backends.

Prerequisites (local):
  docker compose -f docker-compose.dev.yml up -d

Env (see ``.env.example``):
  REDIS_URL, POSTGRES_URL, QDRANT_URL, LANCEDB_URI
  Embedding: ``MISTRAL_API_KEY`` **or** ``EMBED_PROVIDER=lmstudio`` +
  ``LMSTUDIO_BASE_URL`` (+ running local server).

Usage:
  python3 scripts/rag_pipeline_live_smoke.py
  python3 scripts/rag_pipeline_live_smoke.py --bootstrap-only
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import asyncpg
import lancedb
from loguru import logger
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from atlas.models.signal import (
    AgentResult,
    CategoryScores,
    SignalDecision,
    SignalDirection,
    SignalOutput,
    TelemetryEvent,
)
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.pipeline import RAGPipeline
from atlas.rag.qdrant_payload_indexes import keyword_index_field_list
from atlas.shared.config import PolarisSettings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RAGPipeline live smoke (Redis, Postgres, Qdrant, LanceDB, embeddings).",
    )
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="Only run bootstrap (schema + Qdrant keyword indexes), no writes or embeds.",
    )
    parser.add_argument(
        "--asset",
        default="BTCUSDT",
        help="Symbol for sample write/query (default BTCUSDT).",
    )
    return parser.parse_args()


def _demo_signal(asset: str) -> SignalOutput:
    now = datetime.now(timezone.utc)
    return SignalOutput(
        decision=SignalDecision.BUY,
        asset=asset,
        expires_at=now + timedelta(hours=24),
        score=72,
        confidence=Decimal("0.71"),
        category_scores=CategoryScores(technical=72, total=72),
        telemetry=TelemetryEvent(
            cycle_id="rag-live-smoke",
            cycle_latency_ms=12.0,
            agent_count=3,
            timestamp=now,
        ),
        reasoning_summary=(
            "Live RAG smoke: derivatives funding stable, short-term momentum constructive."
        ),
    )


def _demo_agent_results() -> list[AgentResult]:
    return [
        AgentResult(
            agent_name="technical",
            score=55,
            max_score=220,
            direction=SignalDirection.BULLISH,
            explanation="EMA stack aligned on 4h; smoke test row.",
        ),
    ]


async def _run_pipeline_smoke(
    settings: PolarisSettings,
    *,
    bootstrap_only: bool,
    asset: str,
) -> None:
    lance_path = Path(settings.lancedb_uri)
    lance_path.mkdir(parents=True, exist_ok=True)

    redis_client = Redis.from_url(settings.redis_url, socket_connect_timeout=5.0)
    pool = await asyncpg.create_pool(
        settings.postgres_url,
        min_size=1,
        max_size=3,
        command_timeout=30.0,
    )
    from atlas.core.qdrant_client_factory import create_async_qdrant_client

    qdrant_client = create_async_qdrant_client(settings, timeout=30)
    lancedb_conn = await asyncio.to_thread(lancedb.connect, str(lance_path))

    embedding_service = EmbeddingService(settings)
    pipeline = RAGPipeline(
        settings=settings,
        redis_client=redis_client,
        asyncpg_pool=pool,
        qdrant_client=qdrant_client,
        lancedb_conn=lancedb_conn,
        embedding_service=embedding_service,
    )

    logger.info(
        "RAG keyword-indexed payload fields | fields={}",
        ", ".join(keyword_index_field_list()),
    )
    await pipeline.warm_up()

    if bootstrap_only:
        logger.info("bootstrap_only_success | backends=redis,postgres,qdrant,lancedb")
        await qdrant_client.close()
        await pool.close()
        await redis_client.aclose()
        return

    signal = _demo_signal(asset)
    agents = _demo_agent_results()
    cycle_ts = datetime.now(timezone.utc)
    doc_id = await pipeline.write_signal_memory(signal, asset, agents, cycle_ts)
    logger.info("write_signal_memory | doc_id={} | asset={}", doc_id, asset)

    query_text = "momentum and funding constructive"
    hits = await pipeline.query_context(query_text, asset, top_n=3)
    logger.info("query_context | hits={} | top_text_preview={}", len(hits),
                hits[0].text_content[:120] if hits else "none")

    await qdrant_client.close()
    await pool.close()
    await redis_client.aclose()


def main() -> None:
    args = _parse_args()
    settings = PolarisSettings()
    asyncio.run(
        _run_pipeline_smoke(
            settings,
            bootstrap_only=args.bootstrap_only,
            asset=args.asset.strip().upper(),
        ),
    )


if __name__ == "__main__":
    main()
