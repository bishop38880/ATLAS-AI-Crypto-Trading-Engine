from datetime import datetime, timezone
from typing import Any, List, Optional

import asyncpg  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient

from atlas.api.agent_zero_lifecycle_logic import fetch_agent_zero_lifecycle_payload
from atlas.api.schemas import _BaseConfig
from atlas.rag.query import SIGNAL_MEMORY_COLLECTION
from atlas.rag.retrieval_telemetry import list_recent_retrieval_events
from atlas.rag.writer import RAGWriter
from atlas.shared.config import PolarisSettings


router = APIRouter(prefix="/api/memory", tags=["memory"])


# ─── Schemas ─────────────────────────────────────────────────────────────

class LanceDBStats(BaseModel):
    model_config = _BaseConfig
    sync_state: str
    lag_seconds: int
    vector_count: int
    last_sync_iso: str


class RAGStatsData(BaseModel):
    model_config = _BaseConfig
    collection_name: str
    total_documents: int
    archived_count: int
    archived_percent: float
    active_documents: int
    lancedb: LanceDBStats
    embedding_model: str
    embedding_dims: int
    qdrant_health: str
    lancedb_health: str
    last_updated_iso: str
    qdrant_collection_name: str = SIGNAL_MEMORY_COLLECTION
    qdrant_points_count: Optional[int] = None
    pattern_memory_rows: int = 0


class EscoreBand(BaseModel):
    model_config = _BaseConfig
    label: str
    range_min: float
    range_max: float
    count: int
    percent: float
    status: str


class AgentZeroLifecycleData(BaseModel):
    model_config = _BaseConfig
    last_run_iso: Optional[str]
    last_run_relative: str
    next_run_iso: str
    next_run_relative: str
    last_run_results: Optional[dict[str, Any]]
    escore_distribution: List[EscoreBand]
    avg_escore: float
    threshold: float
    collection_health_label: str


class MemoryActivityEntry(BaseModel):
    model_config = _BaseConfig
    id: str
    timestamp: str
    operation: str
    asset: str
    document_count: int
    latency_ms: int
    result: str
    detail: Optional[str] = None


class ContradictionDetail(BaseModel):
    model_config = _BaseConfig
    asset: str
    statement_a: str
    statement_a_age: str
    statement_b: str
    statement_b_age: str
    resolution: str


class VerifierStatsData(BaseModel):
    model_config = _BaseConfig
    documents_verified: int
    passed: int
    passed_percent: float
    repaired: int
    repaired_percent: float
    repaired_detail: Optional[str]
    flagged: int
    flagged_percent: float
    flagged_detail: Optional[str]
    rejected: int
    rejected_percent: float
    rejected_detail: Optional[str]
    contradictions: List[ContradictionDetail]


class RetrievalHitWire(BaseModel):
    model_config = _BaseConfig
    document_id: str
    similarity_score: float
    final_score: float
    asset: str = ""
    signal_decision: str = ""
    preview: str = ""
    user_tags: List[str] = Field(default_factory=list)


class RetrievalEventWire(BaseModel):
    model_config = _BaseConfig
    id: str
    timestamp: str
    asset: str
    query_text: str
    retrieval_depth: str
    hit_count: int
    hits: List[RetrievalHitWire] = Field(default_factory=list)
    error: Optional[str] = None


class StorePreviewSignalWire(BaseModel):
    model_config = _BaseConfig
    signal_id: str
    asset: str
    decision: str
    score: int
    created_at_iso: str
    reasoning_preview: str
    user_tags: List[str] = Field(default_factory=list)


class StorePreviewPatternWire(BaseModel):
    model_config = _BaseConfig
    id: str
    title: str
    category: str
    created_at_iso: str
    content_preview: str
    user_tags: List[str] = Field(default_factory=list)


class StorePreviewData(BaseModel):
    model_config = _BaseConfig
    signals: List[StorePreviewSignalWire]
    patterns: List[StorePreviewPatternWire]


class ManualPatternCreate(BaseModel):
    model_config = _BaseConfig
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20_000)
    category: str = Field(default="user_curated", max_length=120)
    tags: List[str] = Field(default_factory=list)


class ManualPatternResponse(BaseModel):
    model_config = _BaseConfig
    pattern_id: str


class SignalTagRequest(BaseModel):
    model_config = _BaseConfig
    signal_id: str = Field(min_length=1)
    tags: List[str] = Field(min_length=1)
    note: Optional[str] = Field(default=None, max_length=4_000)


class SignalTagResponse(BaseModel):
    model_config = _BaseConfig
    ok: bool


def _get_rag_writer(request: Request) -> RAGWriter:
    writer: RAGWriter | None = getattr(request.app.state, "rag_writer", None)
    if writer is None:
        raise HTTPException(status_code=503, detail="rag_writer_unavailable")
    return writer


# ─── Routes ──────────────────────────────────────────────────────────────


@router.get("/stats", response_model=RAGStatsData, response_model_by_alias=True)
async def get_rag_stats(request: Request) -> RAGStatsData:
    """RAG statistics: PostgreSQL signal/pattern rows + optional Qdrant point count."""
    embed_svc = getattr(request.app.state, "embedding_service", None)
    settings = PolarisSettings()
    embed_dims = (
        int(embed_svc.vector_dimension)
        if embed_svc is not None
        else int(settings.embed_dimensions)
    )
    embed_model = str(settings.embed_model)
    pool: Optional[asyncpg.Pool] = getattr(request.app.state, "db_pool", None)
    qdrant: Optional[AsyncQdrantClient] = getattr(request.app.state, "qdrant_client", None)

    total_docs = 0
    archived_docs = 0
    pattern_rows = 0
    if pool:
        try:
            val_total = await pool.fetchval(
                "SELECT COUNT(*) FROM signal_history",
                timeout=2.0,
            )
            if val_total is not None:
                total_docs = int(val_total)

            val_archived = await pool.fetchval(
                "SELECT COUNT(*) FROM signal_history WHERE outcome_label IS NOT NULL",
                timeout=2.0,
            )
            if val_archived is not None:
                archived_docs = int(val_archived)

            pm = await pool.fetchval(
                "SELECT COUNT(*) FROM pattern_memory",
                timeout=2.0,
            )
            if pm is not None:
                pattern_rows = int(pm)
        except Exception:
            pass

    qdrant_points: Optional[int] = None
    qdrant_health = "UNAVAILABLE"
    if qdrant is not None:
        try:
            info = await qdrant.get_collection(SIGNAL_MEMORY_COLLECTION, timeout=5)
            qdrant_points = int(getattr(info, "points_count", 0) or 0)
            qdrant_health = "HEALTHY"
        except Exception:
            qdrant_health = "DEGRADED"
            qdrant_points = None

    active_docs = total_docs - archived_docs
    archived_percent = (archived_docs / total_docs * 100) if total_docs > 0 else 0.0
    now_iso = datetime.now(timezone.utc).isoformat()

    mirror_vectors = qdrant_points if qdrant_points is not None else total_docs

    return RAGStatsData.model_construct(
        collection_name="signal_history + pattern_memory (PostgreSQL / pgvector)",
        total_documents=total_docs,
        archived_count=archived_docs,
        archived_percent=round(archived_percent, 1),
        active_documents=active_docs,
        lancedb=LanceDBStats.model_construct(
            sync_state="MIRROR_ESTIMATE",
            lag_seconds=0,
            vector_count=mirror_vectors,
            last_sync_iso=now_iso,
        ),
        embedding_model=embed_model,
        embedding_dims=embed_dims,
        qdrant_health=qdrant_health,
        lancedb_health="STANDBY",
        last_updated_iso=now_iso,
        qdrant_collection_name=SIGNAL_MEMORY_COLLECTION,
        qdrant_points_count=qdrant_points,
        pattern_memory_rows=pattern_rows,
    )


@router.get("/agent-zero", response_model=AgentZeroLifecycleData, response_model_by_alias=True)
async def get_agent_zero(request: Request) -> AgentZeroLifecycleData:
    """Return Agent Zero schedule, last run, and Escore distribution."""
    settings = PolarisSettings()
    redis = getattr(request.app.state, "redis", None)
    pool = getattr(request.app.state, "db_pool", None)
    payload = await fetch_agent_zero_lifecycle_payload(redis, pool, settings)
    band_rows = payload.pop("escore_distribution", [])
    bands = [EscoreBand.model_construct(**row) for row in band_rows]
    return AgentZeroLifecycleData.model_construct(
        escore_distribution=bands,
        **payload,
    )


@router.get("/activity", response_model=List[MemoryActivityEntry], response_model_by_alias=True)
async def get_memory_activity(request: Request) -> List[MemoryActivityEntry]:
    """Mirror recent RAG retrievals as activity rows (backward-compatible shape)."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return []
    raw = await list_recent_retrieval_events(redis, limit=20)
    out: list[MemoryActivityEntry] = []
    for item in raw:
        hits_raw = item.get("hits")
        hits_list = hits_raw if isinstance(hits_raw, list) else []
        err = item.get("error")
        result = "ERROR" if err else "OK"
        detail = str(err) if err else None
        try:
            out.append(
                MemoryActivityEntry.model_validate(
                    {
                        "id": str(item.get("id", "")),
                        "timestamp": str(item.get("timestamp", "")),
                        "operation": "rag_retrieval",
                        "asset": str(item.get("asset", "")),
                        "document_count": int(item.get("hitCount", len(hits_list))),
                        "latency_ms": 0,
                        "result": result,
                        "detail": detail,
                    },
                ),
            )
        except Exception:
            continue
    return out


@router.get(
    "/retrievals",
    response_model=List[RetrievalEventWire],
    response_model_by_alias=True,
)
async def get_retrievals(
    request: Request,
    limit: int = Query(default=25, ge=1, le=100),
) -> List[RetrievalEventWire]:
    """Recent vector retrievals with similarity / decay scores (newest first)."""
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        return []
    raw = await list_recent_retrieval_events(redis, limit=limit)
    events: list[RetrievalEventWire] = []
    for item in raw:
        try:
            events.append(RetrievalEventWire.model_validate(item))
        except Exception:
            continue
    return events


@router.get("/store-preview", response_model=StorePreviewData, response_model_by_alias=True)
async def get_store_preview(
    request: Request,
    limit: int = Query(default=12, ge=1, le=50),
) -> StorePreviewData:
    """Latest rows backing embeddings (Postgres), including user tags."""
    pool: Optional[asyncpg.Pool] = getattr(request.app.state, "db_pool", None)
    if pool is None:
        return StorePreviewData.model_construct(signals=[], patterns=[])

    signals_out: list[StorePreviewSignalWire] = []
    patterns_out: list[StorePreviewPatternWire] = []
    try:
        rows = await pool.fetch(
            """
            SELECT signal_id, asset, decision, score, reasoning, created_at, metadata
            FROM signal_history
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
            timeout=3.0,
        )
        for r in rows:
            meta = r["metadata"] if isinstance(r["metadata"], dict) else {}
            tags_raw = meta.get("user_tags", [])
            tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
            created = r["created_at"]
            created_iso = (
                created.isoformat()
                if hasattr(created, "isoformat")
                else str(created)
            )
            reasoning = str(r["reasoning"] or "")[:240]
            signals_out.append(
                StorePreviewSignalWire.model_validate(
                    {
                        "signal_id": str(r["signal_id"]),
                        "asset": str(r["asset"]),
                        "decision": str(r["decision"]),
                        "score": int(r["score"]),
                        "created_at_iso": created_iso,
                        "reasoning_preview": reasoning,
                        "user_tags": tags,
                    },
                ),
            )

        prow = await pool.fetch(
            """
            SELECT id, title, category, content, created_at, metadata
            FROM pattern_memory
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
            timeout=3.0,
        )
        for r in prow:
            meta = r["metadata"] if isinstance(r["metadata"], dict) else {}
            tags_raw = meta.get("user_tags", [])
            tags = [str(t) for t in tags_raw] if isinstance(tags_raw, list) else []
            created = r["created_at"]
            created_iso = (
                created.isoformat()
                if hasattr(created, "isoformat")
                else str(created)
            )
            patterns_out.append(
                StorePreviewPatternWire.model_validate(
                    {
                        "id": str(r["id"]),
                        "title": str(r["title"]),
                        "category": str(r["category"]),
                        "created_at_iso": created_iso,
                        "content_preview": str(r["content"] or "")[:240],
                        "user_tags": tags,
                    },
                ),
            )
    except Exception:
        return StorePreviewData.model_construct(signals=[], patterns=[])

    return StorePreviewData.model_construct(signals=signals_out, patterns=patterns_out)


@router.post(
    "/patterns",
    response_model=ManualPatternResponse,
    response_model_by_alias=True,
)
async def create_manual_pattern(
    request: Request,
    body: ManualPatternCreate,
) -> ManualPatternResponse:
    """Add a curated pattern row (embedded into ``pattern_memory``)."""
    writer = _get_rag_writer(request)
    meta: dict[str, Any] = {"source": "dashboard", "user_tags": list(body.tags)}
    row_id = await writer.write_pattern(
        title=body.title,
        content=body.content,
        category=body.category,
        source="user",
        relevance_score=1.0,
        metadata=meta,
    )
    return ManualPatternResponse.model_construct(pattern_id=row_id)


@router.post(
    "/signal-tags",
    response_model=SignalTagResponse,
    response_model_by_alias=True,
)
async def tag_signal(
    request: Request,
    body: SignalTagRequest,
) -> SignalTagResponse:
    """Attach reviewer tags / notes to an existing ``signal_history`` row."""
    writer = _get_rag_writer(request)
    ok = await writer.tag_signal_metadata(
        body.signal_id,
        [t.strip() for t in body.tags if t.strip()],
        note=body.note,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="signal_not_found")
    return SignalTagResponse.model_construct(ok=True)


@router.get("/verifier", response_model=VerifierStatsData, response_model_by_alias=True)
async def get_verifier_stats(request: Request) -> VerifierStatsData:
    """Return default/zeroed VerifierStatsData."""
    return VerifierStatsData.model_construct(
        documents_verified=0,
        passed=0,
        passed_percent=0.0,
        repaired=0,
        repaired_percent=0.0,
        repaired_detail=None,
        flagged=0,
        flagged_percent=0.0,
        flagged_detail=None,
        rejected=0,
        rejected_percent=0.0,
        rejected_detail=None,
        contradictions=[],
    )