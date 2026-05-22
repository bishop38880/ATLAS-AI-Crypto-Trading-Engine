"""OmniBox SSE stream — LM Studio or DeepSeek chat + optional RAG + Redis snapshot."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import lancedb  # type: ignore[import-untyped]
import msgspec
from loguru import logger
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import FieldCondition, Filter, MatchValue
from redis.asyncio import Redis

from atlas.api.omnibox_provider_context import build_omnibox_redis_provider_context
from atlas.api.omnibox_logic import (
    classify_omnibox_route,
    omnibox_backend_is_deepseek,
    omnibox_context_flags,
    resolved_omnibox_lmstudio_model_id,
)
from atlas.rag.embedding_service import EmbeddingService
from atlas.rag.query import RAGQueryEngine, RetrievalDepth, ScoredDocument
from atlas.shared.config import PolarisSettings

_OMNI_SYSTEM_PROMPT = (
    "You are POLARIS OmniBox, a trading intelligence assistant. "
    "Use the operator question, the asset context, and any supplied "
    "retrieved memory bullets. Cite memory with [n] matching the context "
    "indices. Be precise, avoid guaranteed price claims, and state "
    "uncertainty when data is thin. "
    "When a **Redis provider cache bundle** is present, use it as the "
    "authoritative on-platform snapshot (prices, funding, macro, Fear&Greed, "
    "signal KV, etc.). If no bundle or a field is missing, do not invent "
    "live numbers from prior training—say the cache did not have that slice. "
    "Format with markdown: **bold** for emphasis, backticks for symbols."
)


def _sse_event(payload: dict[str, Any]) -> str:
    encoded = msgspec.json.encode(payload).decode("utf-8")
    return f"data: {encoded}\n\n"


def _deepseek_chat_url(settings: PolarisSettings) -> str:
    base = settings.deepseek_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/v1/chat/completions"


def _lmstudio_chat_url(settings: PolarisSettings) -> str:
    base = settings.lmstudio_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return "{}/chat/completions".format(base)


def _lmstudio_authorization_bearer(settings: PolarisSettings) -> str:
    for candidate in (
        settings.embed_api_key.get_secret_value(),
        settings.deepseek_api_key.get_secret_value(),
    ):
        trimmed = candidate.strip()
        if trimmed:
            return trimmed
    return "lm-studio"


def _asset_filter(symbol: str) -> Filter | None:
    raw = symbol.strip().upper()
    base = raw.split("/")[0] if raw else ""
    if not base:
        return None
    variants = list({base, "{}/USDT".format(base)})
    if len(variants) == 1:
        return Filter(
            must=[FieldCondition(key="asset", match=MatchValue(value=variants[0]))],
        )
    return Filter(
        should=[
            FieldCondition(key="asset", match=MatchValue(value=v)) for v in variants
        ],
    )


def _chunk_sse_text(answer: str) -> list[str]:
    if not answer:
        return []
    chunks: list[str] = []
    buf: list[str] = []
    for ch in answer:
        buf.append(ch)
        if len(buf) >= 56:
            chunks.append("".join(buf))
            buf = []
    if buf:
        chunks.append("".join(buf))
    return chunks


def _format_rag_block(docs: list[ScoredDocument]) -> str:
    lines: list[str] = []
    for i, doc in enumerate(docs, start=1):
        payload = doc.payload or {}
        summary = payload.get("reasoning_summary") or payload.get("summary") or ""
        if not summary and payload:
            summary = str(payload)[:240]
        lines.append(
            "[{}] asset={} decision={} score={} sim={:.3f} — {}".format(
                i,
                payload.get("asset", "?"),
                payload.get("decision", "?"),
                payload.get("score", "?"),
                float(doc.similarity_score),
                summary,
            ),
        )
    return "\n".join(lines)


def _sources_from_docs(
    docs: list[ScoredDocument],
    fallback_asset: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx, doc in enumerate(docs, start=1):
        payload = doc.payload or {}
        title = str(
            payload.get("title")
            or payload.get("headline")
            or f"Polaris memory match #{idx}",
        )
        snippet = str(
            payload.get("reasoning_summary")
            or payload.get("summary")
            or payload.get("snippet")
            or "",
        )[:400]
        asset_val = str(payload.get("asset") or fallback_asset or "")
        ts = doc.timestamp.isoformat().replace("+00:00", "Z")
        row: dict[str, Any] = {
            "id": str(idx),
            "title": title,
            "snippet": snippet,
            "asset": asset_val,
            "timestampUtc": ts,
            "scoreMax": 220,
        }
        score_raw = payload.get("score")
        if isinstance(score_raw, (int, float)):
            row["score"] = int(score_raw)
        decision_raw = payload.get("decision")
        if isinstance(decision_raw, str):
            row["decision"] = decision_raw
        rows.append(row)
    return rows


async def _try_build_rag_engine(
    settings: PolarisSettings,
    embed: EmbeddingService,
) -> RAGQueryEngine | None:
    client: AsyncQdrantClient | None = None
    try:
        from atlas.core.qdrant_client_factory import create_async_qdrant_client

        client = create_async_qdrant_client(settings, timeout=5)
        await asyncio.wait_for(client.get_collections(), timeout=5.0)
        lance_conn = lancedb.connect(settings.lancedb_uri)
        return RAGQueryEngine(settings, client, lance_conn, embed)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("omnibox_rag_engine_unavailable | err={}", str(exc))
        if client is not None:
            await client.close()
        return None


async def _retrieve_rag_documents(
    settings: PolarisSettings,
    embedding_service: EmbeddingService,
    text: str,
    asset: str,
) -> list[ScoredDocument]:
    engine = await _try_build_rag_engine(settings, embedding_service)
    if engine is None:
        return []
    try:
        flt = _asset_filter(asset)
        docs = await asyncio.wait_for(
            engine.find_similar_contexts(
                text,
                retrieval_depth=RetrievalDepth.SHALLOW,
                qdrant_filter=flt,
            ),
            timeout=14.0,
        )
        if not docs and flt is not None:
            docs = await asyncio.wait_for(
                engine.find_similar_contexts(
                    text,
                    retrieval_depth=RetrievalDepth.SHALLOW,
                    qdrant_filter=None,
                ),
                timeout=14.0,
            )
        return docs
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("omnibox_rag_retrieval_failed | err={}", str(exc))
        return []
    finally:
        await engine.aclose()


async def _complete_deepseek(
    settings: PolarisSettings,
    http_client: httpx.AsyncClient,
    system: str,
    user_message: str,
) -> tuple[str, int]:
    headers = {
        "Authorization": "Bearer {}".format(settings.deepseek_api_key.get_secret_value()),
        "Content-Type": "application/json",
    }
    max_out = min(int(settings.deepseek_max_tokens), 2048)
    body = {
        "model": settings.deepseek_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "temperature": float(settings.deepseek_temperature),
        "max_tokens": max_out,
    }
    t0 = time.perf_counter()
    resp = await http_client.post(
        _deepseek_chat_url(settings),
        content=msgspec.json.encode(body),
        headers=headers,
    )
    resp.raise_for_status()
    data = msgspec.json.decode(resp.content)
    text = str(data["choices"][0]["message"].get("content") or "")
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return text, latency_ms


async def _complete_lmstudio(
    settings: PolarisSettings,
    http_client: httpx.AsyncClient,
    system: str,
    user_message: str,
) -> tuple[str, int]:
    model_id = resolved_omnibox_lmstudio_model_id(settings)
    headers = {
        "Authorization": "Bearer {}".format(_lmstudio_authorization_bearer(settings)),
        "Content-Type": "application/json",
    }
    max_out = min(int(settings.local_max_tokens), 2048)
    body = {
        "model": model_id,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
        "temperature": float(settings.router_temperature),
        "max_tokens": max_out,
    }
    t0 = time.perf_counter()
    resp = await http_client.post(
        _lmstudio_chat_url(settings),
        content=msgspec.json.encode(body),
        headers=headers,
    )
    resp.raise_for_status()
    data = msgspec.json.decode(resp.content)
    text = str(data["choices"][0]["message"].get("content") or "")
    latency_ms = int((time.perf_counter() - t0) * 1000)
    return text, latency_ms


async def stream_omnibox_answer(
    *,
    settings: PolarisSettings,
    embedding_service: EmbeddingService,
    redis: Redis,
    http_client: httpx.AsyncClient,
    query: str,
    asset: str,
    route_override: str | None,
) -> AsyncIterator[str]:
    route, rationale = classify_omnibox_route(query, route_override)
    confidence = 0.82 if route_override is None else 1.0
    yield _sse_event(
        {
            "type": "classification",
            "data": {"route": route, "confidence": confidence, "rationale": rationale},
        },
    )

    use_rag, _use_live = omnibox_context_flags(route)
    rag_docs: list[ScoredDocument] = []
    if use_rag:
        rag_docs = await _retrieve_rag_documents(settings, embedding_service, query, asset)

    provider_bundle: str | None = None
    try:
        provider_bundle = await asyncio.wait_for(
            build_omnibox_redis_provider_context(redis, settings, asset),
            timeout=5.0,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("omnibox_provider_bundle_failed | err={}", str(exc))
        provider_bundle = None

    user_parts = [
        "Asset focus: {}".format(asset),
        "Operator question:\n{}".format(query.strip()),
    ]
    if provider_bundle:
        user_parts.append(provider_bundle)
    if rag_docs:
        user_parts.append(
            "Retrieved POLARIS signal memory (verify externally):\n{}".format(
                _format_rag_block(rag_docs),
            ),
        )
    user_message = "\n\n".join(user_parts)

    use_deepseek = omnibox_backend_is_deepseek(settings)
    lm_model_id = resolved_omnibox_lmstudio_model_id(settings)
    tier_label = "LM Studio · {}".format(lm_model_id)
    latency_ms = 0
    answer = ""

    if use_deepseek:
        tier_label = "DeepSeek · {}".format(settings.deepseek_model)
        api_key = settings.deepseek_api_key.get_secret_value().strip()
        if not api_key:
            msg = (
                "DeepSeek OmniBox backend is selected (OMNIBOX_CHAT_BACKEND=deepseek) but "
                "DEEPSEEK_API_KEY is empty. Set it or switch back to LM Studio "
                '("lmstudio").'
            )
            for piece in _chunk_sse_text(msg):
                yield _sse_event({"type": "token", "data": piece})
            if rag_docs:
                yield _sse_event(
                    {"type": "sources", "data": _sources_from_docs(rag_docs, asset)},
                )
            yield _sse_event(
                {
                    "type": "done",
                    "data": {
                        "latencyMs": 0,
                        "llmTier": "UNCONFIGURED",
                        "liveDataSummary": provider_bundle,
                    },
                },
            )
            return
        try:
            answer, latency_ms = await _complete_deepseek(
                settings,
                http_client,
                _OMNI_SYSTEM_PROMPT,
                user_message,
            )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "omnibox_deepseek_http | status={} | body={}",
                exc.response.status_code,
                exc.response.text[:400],
            )
            answer = (
                "DeepSeek returned HTTP {} — check API key, model name, and quota.".format(
                    exc.response.status_code,
                )
            )
        except Exception as exc:
            logger.exception("omnibox_deepseek_failed | err={}", str(exc))
            answer = (
                "OmniBox could not reach DeepSeek. Confirm network and credentials."
            )
    else:
        try:
            answer, latency_ms = await _complete_lmstudio(
                settings,
                http_client,
                _OMNI_SYSTEM_PROMPT,
                user_message,
            )
        except asyncio.CancelledError:
            raise
        except httpx.HTTPStatusError as exc:
            logger.error(
                "omnibox_lmstudio_http | status={} | body={} | endpoint={}",
                exc.response.status_code,
                exc.response.text[:400],
                _lmstudio_chat_url(settings),
            )
            answer = (
                "LM Studio chat failed (HTTP {}). "
                "Ensure a model such as '{}' is loaded and {} is reachable.".format(
                    exc.response.status_code,
                    lm_model_id,
                    settings.lmstudio_base_url.rstrip("/"),
                )
            )
        except Exception as exc:
            logger.exception("omnibox_lmstudio_failed | err={}", str(exc))
            answer = (
                "OmniBox could not reach LM Studio at {} (model {}). "
                "Confirm the server is running and LMSTUDIO_BASE_URL is correct.".format(
                    _lmstudio_chat_url(settings),
                    lm_model_id,
                )
            )

    for piece in _chunk_sse_text(answer):
        yield _sse_event({"type": "token", "data": piece})

    if rag_docs:
        yield _sse_event(
            {"type": "sources", "data": _sources_from_docs(rag_docs, asset)},
        )

    yield _sse_event(
        {
            "type": "done",
            "data": {
                "latencyMs": latency_ms,
                "llmTier": tier_label,
                "liveDataSummary": provider_bundle,
            },
        },
    )

