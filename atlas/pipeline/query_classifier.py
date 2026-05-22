"""Async query classifier for RAG/MCP/direct routing.

Classifies incoming reasoning requests so the correct retrieval
path is used before any LLM call.  Prevents wasted RAG calls
and prevents MCP calls on historical queries.

Routes:
    RAG_ONLY  — historical / analytical  (semantic retrieval)
    MCP_ONLY  — real-time data           (live provider MCP)
    HYBRID    — both historical + live
    DIRECT    — no retrieval; context window only (CAG)
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Literal

import msgspec
import redis.asyncio as redis_async
from loguru import logger
from pydantic import BaseModel, ConfigDict
from atlas.rag.query import RetrievalDepth


# ---------------------------------------------------------------------------
# Module-level constants (importable for tests)
# ---------------------------------------------------------------------------

DIRECT_KEYWORDS: tuple[str, ...] = (
    "explain",
    "what is",
    "define",
    "how does",
    "what are",
    "describe",
    "tell me about",
    "what does",
)

REALTIME_KEYWORDS: tuple[str, ...] = (
    "current",
    "now",
    "latest",
    "real-time",
    "live",
    "right now",
    "at the moment",
    "today",
    "this minute",
    "is it",
)

HISTORICAL_KEYWORDS: tuple[str, ...] = (
    "last time",
    "historically",
    "historical",
    "past",
    "previous",
    "similar setup",
    "pattern",
    "when did",
    "what happened",
    "has this happened",
    "similar conditions",
    "like this before",
)

HYBRID_PHRASES: tuple[str, ...] = (
    "compare current",
    "vs history",
    "relative to historical",
    "current vs",
    "compare to past",
)

MONITORED_ASSETS: frozenset[str] = frozenset({
    "BTC", "ETH", "SOL", "BNB", "XRP", "AVAX",
    "DOGE", "LINK", "SUI", "INJ", "TAO", "RENDER",
    "ICP", "HBAR", "WIF", "PEPE", "ARB", "OP",
    "POL", "NEAR", "APT", "SEI", "TIA", "PYTH",
    "JUP", "PENDLE", "WLD", "STRK", "MANTA", "ALT",
    "PIXEL", "PORTAL", "MYRO",
})

CACHE_TTL_SECONDS: int = 300


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class QueryClassification(BaseModel):
    """Immutable classification result for a query."""

    model_config = ConfigDict(frozen=True)

    route: Literal["RAG_ONLY", "MCP_ONLY", "HYBRID", "DIRECT"]
    confidence: float
    detected_signals: list[str]
    fallback_to_hybrid: bool = False
    cache_hit: bool = False
    query_hash: str
    retrieval_depth: RetrievalDepth = RetrievalDepth.DEFAULT


class _CachePayload(msgspec.Struct, frozen=True):
    """Lightweight struct for Redis cache serialization."""

    route: str
    confidence: float
    detected_signals: list[str]
    fallback_to_hybrid: bool
    query_hash: str
    retrieval_depth: str


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class QueryClassifier:
    """Classify queries into retrieval routes before LLM calls."""

    def __init__(
        self,
        redis_client: redis_async.Redis,  # type: ignore[type-arg]
    ) -> None:
        self._redis = redis_client

    async def classify(
        self,
        query_text: str,
        asset: str | None = None,
    ) -> QueryClassification:
        """Classify *query_text* and return the routing decision."""
        query_hash = self._hash_query(query_text)
        cached = await self._get_cached_classification(query_hash)
        if cached is not None:
            return cached

        normalised = query_text.lower().strip()
        result = _classify_normalised(normalised, query_hash)
        await self._cache_classification(query_hash, result)
        return result

    # ── Cache helpers ─────────────────────────────────────────────

    async def _get_cached_classification(
        self,
        query_hash: str,
    ) -> QueryClassification | None:
        """Best-effort Redis cache read."""
        try:
            raw = await asyncio.wait_for(
                self._redis.get(f"qclassify:{query_hash}"),
                timeout=5.0,
            )
        except Exception:
            logger.debug("cache_read_failed | hash={}", query_hash)
            return None
        if not raw:
            return None
        try:
            payload = msgspec.json.decode(raw, type=_CachePayload)
            return _payload_to_classification(payload, cache_hit=True)
        except Exception:
            logger.debug("cache_decode_failed | hash={}", query_hash)
            return None

    async def _cache_classification(
        self,
        query_hash: str,
        result: QueryClassification,
    ) -> None:
        """Best-effort Redis cache write with TTL."""
        payload = _classification_to_payload(result)
        try:
            await asyncio.wait_for(
                self._redis.set(
                    f"qclassify:{query_hash}",
                    msgspec.json.encode(payload),
                    ex=CACHE_TTL_SECONDS,
                ),
                timeout=5.0,
            )
        except Exception:
            logger.debug("cache_write_failed | hash={}", query_hash)

    # ── Hash ──────────────────────────────────────────────────────

    @classmethod
    def _hash_query(cls, query_text: str) -> str:
        """First 16 hex chars of SHA-256 of lowercased+stripped query."""
        clean = query_text.strip().lower()
        return hashlib.sha256(clean.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Pure classification logic (Steps A–E, first match wins)
# ---------------------------------------------------------------------------


def _classify_normalised(
    normalised: str,
    query_hash: str,
) -> QueryClassification:
    """Apply Steps A–E classification on normalised text."""
    flags, detected = _detect_keywords(normalised)

    # Step A — DIRECT
    if flags["direct"] and not flags["asset"]:
        return _build("DIRECT", 0.95, detected, query_hash)

    # Step B — HYBRID (explicit phrase or both realtime+historical)
    if flags["hybrid"] or (flags["realtime"] and flags["historical"]):
        return _build("HYBRID", 0.85, detected, query_hash)

    # Step C — REAL-TIME
    if flags["realtime"]:
        return _build("MCP_ONLY", 0.90, detected, query_hash)

    # Step D — HISTORICAL
    if flags["historical"]:
        return _build("RAG_ONLY", 0.88, detected, query_hash)

    # Step E — FALLBACK
    logger.warning(
        "no_keywords_matched | query={} | reason={}",
        normalised[:80],
        "no keywords matched",
    )
    return _build("HYBRID", 0.60, detected, query_hash, fallback=True)


def _detect_keywords(
    normalised: str,
) -> tuple[dict[str, bool], list[str]]:
    """Detect keyword presence and build detected-tag list."""
    flags = {
        "direct": _has_any(normalised, DIRECT_KEYWORDS),
        "realtime": _has_any(normalised, REALTIME_KEYWORDS),
        "historical": _has_any(normalised, HISTORICAL_KEYWORDS),
        "hybrid": _has_any(normalised, HYBRID_PHRASES),
        "asset": _mentions_monitored_asset(normalised),
    }
    detected: list[str] = [k for k in ("direct", "realtime", "historical", "hybrid_phrase")
                           if flags.get(k.replace("_phrase", ""), False)]
    return flags, detected


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    """Return True if *text* contains any phrase."""
    return any(phrase in text for phrase in phrases)


def _mentions_monitored_asset(normalised: str) -> bool:
    """Check if normalised query mentions any MONITORED_ASSETS symbol."""
    return any(
        asset.lower() in normalised for asset in MONITORED_ASSETS
    )


def _build(
    route: Literal["RAG_ONLY", "MCP_ONLY", "HYBRID", "DIRECT"],
    confidence: float,
    detected: list[str],
    query_hash: str,
    *,
    fallback: bool = False,
) -> QueryClassification:
    """Construct a QueryClassification."""
    return QueryClassification(
        route=route,
        confidence=confidence,
        detected_signals=detected,
        fallback_to_hybrid=fallback,
        cache_hit=False,
        query_hash=query_hash,
        retrieval_depth=RetrievalDepth.DEFAULT,
    )


def _payload_to_classification(
    p: _CachePayload,
    *,
    cache_hit: bool,
) -> QueryClassification:
    """Convert cache payload back to classification."""
    return QueryClassification(
        route=p.route,  # type: ignore[arg-type]
        confidence=p.confidence,
        detected_signals=p.detected_signals,
        fallback_to_hybrid=p.fallback_to_hybrid,
        cache_hit=cache_hit,
        query_hash=p.query_hash,
        retrieval_depth=RetrievalDepth(p.retrieval_depth),
    )


def _classification_to_payload(
    c: QueryClassification,
) -> _CachePayload:
    """Convert classification to cache payload."""
    return _CachePayload(
        route=c.route,
        confidence=c.confidence,
        detected_signals=c.detected_signals,
        fallback_to_hybrid=c.fallback_to_hybrid,
        query_hash=c.query_hash,
        retrieval_depth=c.retrieval_depth.value,
    )
