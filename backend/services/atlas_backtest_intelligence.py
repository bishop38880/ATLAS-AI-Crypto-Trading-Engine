"""Dashboard intelligence orchestration for POLARIS ATLAS (read-only synthesis).

Despite the historical filename, this package performs **no** backtest simulation,
order management, position sizing, or execution. It aggregates cached signal
metadata, recomputes / echoes confluence on v2.3 caps, attaches RAG context,
and optionally streams results to Redis for the dashboard.

Any attempt to merge execution or position payloads raises ``ValueError``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Final

import msgspec
from loguru import logger
from redis.asyncio import Redis

from atlas.api.polaris_signals_redis_keys import polaris_signal_redis_keys
from atlas.shared.config import PolarisSettings
from backend.cache.vector_store_adapter import VectorStoreAdapter
from backend.schemas.atlas_signals import (
    AtlasDashboardIntelligencePayloadV23,
    ConfluenceV23PipelineInput,
    CURRENT_INTELLIGENCE_SCHEMA,
    HistoricalSignalMetadataV23,
    RagContextBundleV23,
)
from backend.scoring.confluence_engine_v23 import compute_confluence_v23

_FORBIDDEN_SUPPLEMENT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "execution",
        "position",
        "positions",
        "order",
        "orders",
        "fill",
        "fills",
        "portfolio",
    },
)


def _millis_utc_now() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _coerce_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _timestamp_to_millis(raw: Any) -> int:
    if isinstance(raw, (int, float)):
        if raw > 1_000_000_000_000:
            return int(raw)
        return int(raw * 1000)
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except ValueError:
            pass
    return _millis_utc_now()


def category_scores_to_v23_input(row: dict[str, Any]) -> ConfluenceV23PipelineInput:
    """Fold ``SignalOutput.category_scores`` lanes into five v2.3 pillars (integer-only)."""
    cs = row.get("category_scores")
    if not isinstance(cs, dict):
        return ConfluenceV23PipelineInput()

    d_raw = _coerce_int(cs.get("derivatives"))
    d_raw += _coerce_int(cs.get("funding"))
    d_raw += _coerce_int(cs.get("liquidation"))

    o_raw = _coerce_int(cs.get("onchain"))
    o_raw += _coerce_int(cs.get("whale"))

    t_raw = _coerce_int(cs.get("technical"))

    s_raw = _coerce_int(cs.get("sentiment"))
    s_raw += _coerce_int(cs.get("news_macro"))

    m_raw = _coerce_int(cs.get("regime"))
    m_raw += _coerce_int(cs.get("correlation"))
    m_raw += _coerce_int(cs.get("macro"))
    m_raw += _coerce_int(cs.get("context"))

    return ConfluenceV23PipelineInput(
        derivatives_raw=d_raw,
        onchain_raw=o_raw,
        technical_raw=t_raw,
        sentiment_raw=s_raw,
        market_context_raw=m_raw,
    )


def assert_supplement_exec_free(supplement: dict[str, Any] | None) -> None:
    """Reject caller-supplied dicts that try to blend execution or holdings state."""
    if not supplement:
        return
    lowered = {str(k).lower() for k in supplement.keys()}
    blocked = lowered & _FORBIDDEN_SUPPLEMENT_KEYS
    if blocked:
        raise ValueError(
            "execution_context_injection_rejected | keys={}".format(sorted(blocked)),
        )


class AtlasDashboardIntelligenceOrchestrator:
    """Coordinates Redis signal cache + v2.3 scoring + vector retrieval + Redis fan-out."""

    def __init__(
        self,
        settings: PolarisSettings,
        vector_adapter: VectorStoreAdapter,
    ) -> None:
        assert isinstance(settings, PolarisSettings), "settings required"
        assert isinstance(vector_adapter, VectorStoreAdapter), "vector adapter required"
        self._settings = settings
        self._vectors = vector_adapter

    async def load_cached_signal_row(self, redis: Redis, asset: str) -> tuple[dict[str, Any] | None, str | None]:
        """Fetch first available ``polaris:signals:*`` JSON blob for ``asset``."""
        assert isinstance(asset, str) and len(asset.strip()) >= 1, "non-empty asset"
        for key in polaris_signal_redis_keys(asset.strip()):
            raw = await redis.get(key)
            if raw is None:
                continue
            try:
                row = msgspec.json.decode(raw)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    "intelligence_signal_json_decode_failed | key={} | err={}",
                    key,
                    str(exc),
                )
                continue
            if isinstance(row, dict):
                return row, key
        logger.info("intelligence_signal_cache_miss | asset={}", asset)
        return None, None

    def build_historical_metadata(
        self,
        row: dict[str, Any] | None,
        asset: str,
        redis_key: str | None,
    ) -> HistoricalSignalMetadataV23:
        """Project immutable intelligence metadata — no ``action`` or execution fields."""
        if row is None:
            return HistoricalSignalMetadataV23(
                signal_id="unknown",
                asset=asset.strip().upper(),
                timeframe="30m",
                timestamp_millis=_millis_utc_now(),
                normalized_score_0_100=0,
                raw_confluence_reference_0_220=0,
                key_convergences=tuple(),
                key_risks=tuple(),
                redis_source_key=redis_key,
            )

        sid = str(row.get("signal_id") or "unknown")
        ts = _timestamp_to_millis(row.get("timestamp"))
        norm = _coerce_int(row.get("score"), 0)
        norm = max(0, min(100, norm))
        raw220 = _coerce_int(row.get("raw_confluence_score"), 0)
        raw220 = max(0, min(220, raw220))

        conv = row.get("key_convergences") or []
        risks = row.get("key_risks") or []
        if not isinstance(conv, list):
            conv = []
        if not isinstance(risks, list):
            risks = []

        tf = str(row.get("timeframe") or "30m")
        wire_asset = str(row.get("asset") or asset).strip().upper()

        return HistoricalSignalMetadataV23(
            signal_id=sid,
            asset=wire_asset,
            timeframe=tf,
            timestamp_millis=ts,
            normalized_score_0_100=norm,
            raw_confluence_reference_0_220=raw220,
            key_convergences=tuple(str(x) for x in conv if isinstance(x, str)),
            key_risks=tuple(str(x) for x in risks if isinstance(x, str)),
            redis_source_key=redis_key,
        )

    async def assemble_dashboard_payload(
        self,
        redis: Redis,
        asset: str,
        rag_query: str,
        rag_keyword: str | None = None,
        sentiment_gate_active: bool = True,
        confluence_override: ConfluenceV23PipelineInput | None = None,
        caller_supplement: dict[str, Any] | None = None,
    ) -> AtlasDashboardIntelligencePayloadV23:
        """Build ``AtlasDashboardIntelligencePayloadV23`` for HTTP/WebSocket consumers."""
        assert_supplement_exec_free(caller_supplement)

        row, key = await self.load_cached_signal_row(redis, asset)
        historical = self.build_historical_metadata(row, asset, key)

        if confluence_override is None:
            base = category_scores_to_v23_input(row or {})
            pipe_in = base.model_copy(
                update={"sentiment_gate_active": sentiment_gate_active},
            )
        else:
            pipe_in = confluence_override.model_copy(
                update={"sentiment_gate_active": sentiment_gate_active},
            )

        confluence = compute_confluence_v23(pipe_in)

        rag = await self._vectors.hybrid_search_context(
            asset=historical.asset,
            query=rag_query,
            keyword=rag_keyword,
            limit=self._settings.rag_default_top_k,
        )

        notes: list[str] = []
        if row is None:
            notes.append("historical_cache_miss")
        if not rag.chunks:
            notes.append("rag_empty")

        return AtlasDashboardIntelligencePayloadV23(
            schema_version=CURRENT_INTELLIGENCE_SCHEMA,
            generated_at_millis=_millis_utc_now(),
            asset=historical.asset,
            historical=historical,
            confluence=confluence,
            rag=rag,
            pipeline_notes=tuple(notes),
        )

    async def publish_payload(self, redis: Redis, payload: AtlasDashboardIntelligencePayloadV23) -> None:
        """Fan-out encoded JSON to versioned Redis pub/sub + snapshot key."""
        await self._vectors.publish_intelligence_snapshot(
            redis,
            payload.model_dump(mode="json"),
        )
        logger.info(
            "intelligence_payload_published | asset={} | schema={}",
            payload.asset,
            payload.schema_version,
        )


__all__ = [
    "AtlasDashboardIntelligenceOrchestrator",
    "assert_supplement_exec_free",
    "category_scores_to_v23_input",
]
