"""Executive control routes — deep analysis endpoint.

POST /api/executive/analyze/{asset}
    Reads the most recent SignalOutput for the given asset from Redis,
    decodes it with msgspec, and returns the full payload including
    agent_breakdown and deepseek_evaluation for the Deep Analysis Modal.

No trade execution logic lives here — ATLAS only surfaces intelligence.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import msgspec
import redis.asyncio as redis_asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.api.polaris_signals_redis_keys import polaris_signal_redis_keys
from atlas.dependencies import get_redis


router = APIRouter(prefix="/api/executive", tags=["executive"])

# Matches ``atlas.routes.rotation.REDIS_KEYS`` — dashboard ladder for explicit hydrates.
_ROTATION_ACTIVE_33_KEY = "polaris:rotation:active_33"


class AnalyzeResponse(BaseModel):
    """Envelope returned by the analyze endpoint."""

    model_config = ConfigDict(frozen=True)

    asset: str
    signal: dict[str, Any]
    fetched_at: datetime


class AssetAnalysisMonitorResponse(BaseModel):
    """Latest LLM and agent-analysis outputs grouped by asset."""

    model_config = ConfigDict(frozen=True)

    fetched_at: datetime
    asset_count: int
    analyses: list[dict[str, Any]] = Field(default_factory=list)


@router.get(
    "/analysis-monitor",
    response_model=AssetAnalysisMonitorResponse,
)
async def analysis_monitor(
    redis: redis_asyncio.Redis = Depends(get_redis),  # type: ignore[type-arg]
    limit: int = Query(default=48, ge=1, le=100),
) -> AssetAnalysisMonitorResponse:
    """Return compact per-asset LLM reasoning and agent analysis outputs."""
    payloads = await _read_latest_signal_payloads(redis, limit)
    analyses = [_build_analysis_monitor_item(payload) for payload in payloads]
    analyses.sort(key=lambda item: item["timestampEpoch"], reverse=True)

    return AssetAnalysisMonitorResponse(
        fetched_at=datetime.now(timezone.utc),
        asset_count=len(analyses),
        analyses=analyses,
    )


async def _read_latest_signal_payloads(
    redis: redis_asyncio.Redis,  # type: ignore[type-arg]
    limit: int,
) -> list[dict[str, Any]]:
    """Read latest signal payloads from Redis without failing the route."""
    payloads: dict[str, dict[str, Any]] = {}

    rotation_symbols = await _rotation_active_33_symbols(redis)
    for symbol in rotation_symbols:
        if len(payloads) >= limit:
            break
        fetched = await _get_signal_payload_for_rotation_symbol(redis, symbol)
        if fetched is None:
            continue
        payload, dedupe_key = fetched
        payloads[dedupe_key] = payload

    async for key in redis.scan_iter(match="polaris:signals:*"):
        if len(payloads) >= limit:
            break
        raw = await redis.get(key)
        payload = _decode_signal_payload(raw)
        if payload is None:
            continue
        asset = str(payload.get("asset") or _asset_from_signal_key(key))
        payloads[asset] = payload

    if not payloads:
        payload = _decode_signal_payload(await redis.get("polaris:latest_signal"))
        if payload is not None:
            asset = str(payload.get("asset", "UNKNOWN"))
            payloads[asset] = payload

    return list(payloads.values())


async def _rotation_active_33_symbols(redis: redis_asyncio.Redis) -> list[str]:  # type: ignore[type-arg]
    """Sorted ladder symbols backing the 33-card dashboard (may be empty if stale)."""
    try:
        raw_members = await redis.smembers(_ROTATION_ACTIVE_33_KEY)
    except Exception:
        return []
    if not raw_members:
        return []
    decoded: list[str] = []
    for member in raw_members:
        if isinstance(member, bytes):
            decoded.append(member.decode("utf-8"))
        else:
            decoded.append(str(member))
    return sorted(decoded)


async def _get_signal_payload_for_rotation_symbol(
    redis: redis_asyncio.Redis,  # type: ignore[type-arg]
    symbol: str,
) -> tuple[dict[str, Any], str] | None:
    """Resolve ``polaris:signals:*`` for one rotation entry using pair/USDT aliases."""
    for redis_key in polaris_signal_redis_keys(symbol):
        raw = await redis.get(redis_key)
        payload = _decode_signal_payload(raw)
        if payload is None:
            continue
        dedupe_key = str(payload.get("asset") or _asset_from_signal_key(redis_key))
        return (payload, dedupe_key)
    return None


def _decode_signal_payload(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    try:
        payload = msgspec.json.decode(raw)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        logger.warning("analysis_monitor_decode_failed | exc={}", exc)
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _asset_from_signal_key(key: Any) -> str:
    decoded_key = key.decode("utf-8") if isinstance(key, bytes) else str(key)
    return decoded_key.removeprefix("polaris:signals:")


def _build_analysis_monitor_item(signal: dict[str, Any]) -> dict[str, Any]:
    deepseek = signal.get("deepseek_evaluation")
    if not isinstance(deepseek, dict):
        deepseek = {}

    agent_breakdown = signal.get("agent_breakdown")
    if not isinstance(agent_breakdown, dict):
        agent_breakdown = {}

    return {
        "asset": str(signal.get("asset", "UNKNOWN")),
        "timestamp": str(signal.get("timestamp", "")),
        "timestampEpoch": _timestamp_epoch(signal.get("timestamp")),
        "decision": str(signal.get("decision", "Hold")),
        "score": _coerce_float(signal.get("score"), 0.0),
        "rawConfluenceScore": _coerce_float(signal.get("raw_confluence_score"), 0.0),
        "confidence": _coerce_float(signal.get("confidence"), 0.0),
        "pipelineConfidence": _coerce_float(signal.get("pipeline_confidence"), 0.0),
        "confidenceTier": str(signal.get("confidence_tier", "UNKNOWN")),
        "reasoningSummary": str(signal.get("reasoning_summary", "")),
        "keyConvergences": _string_list(signal.get("key_convergences")),
        "keyRisks": _string_list(signal.get("key_risks")),
        "llm": _build_llm_monitor_item(deepseek),
        "agents": _build_agent_monitor_items(agent_breakdown),
    }


def _build_llm_monitor_item(deepseek: dict[str, Any]) -> dict[str, Any]:
    return {
        "invoked": bool(deepseek),
        "decision": str(deepseek.get("decision", "Not invoked")),
        "confidence": _coerce_float(deepseek.get("confidence"), 0.0),
        "crossCorrelationGrade": str(deepseek.get("cross_correlation_grade", "UNKNOWN")),
        "reasoning": str(deepseek.get("reasoning", "")),
        "wouldChangeIf": str(deepseek.get("would_change_if", "")),
        "keyConvergences": _string_list(deepseek.get("key_convergences")),
        "keyRisks": _string_list(deepseek.get("key_risks")),
    }


def _build_agent_monitor_items(agent_breakdown: dict[str, Any]) -> list[dict[str, Any]]:
    agents: list[dict[str, Any]] = []
    for agent_name, raw_agent in agent_breakdown.items():
        if not isinstance(raw_agent, dict):
            continue
        agents.append(
            {
                "name": str(raw_agent.get("agent_name", agent_name)),
                "score": _coerce_float(raw_agent.get("score"), 0.0),
                "maxScore": _coerce_float(raw_agent.get("max_score"), 0.0),
                "direction": str(raw_agent.get("direction", "neutral")),
                "explanation": str(raw_agent.get("explanation", "")),
                "risks": _string_list(raw_agent.get("risks")),
                "convergences": _string_list(raw_agent.get("convergences")),
                "subSignals": _normalise_sub_signals(raw_agent.get("sub_signals")),
            }
        )
    return agents


def _normalise_sub_signals(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    sub_signals: list[dict[str, Any]] = []
    for name, raw_signal in value.items():
        if isinstance(raw_signal, dict):
            sub_signals.append(
                {
                    "name": str(name),
                    "value": raw_signal.get("value", ""),
                    "flag": str(raw_signal.get("flag", "INFO")),
                }
            )
        else:
            sub_signals.append({"name": str(name), "value": raw_signal, "flag": "INFO"})
    return sub_signals


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timestamp_epoch(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0
    return 0.0


@router.post(
    "/analyze/{asset}",
    response_model=AnalyzeResponse,
)
async def analyze_asset(
    asset: str,
    redis: redis_asyncio.Redis = Depends(get_redis),  # type: ignore[type-arg]
) -> AnalyzeResponse:
    """Return the full cached SignalOutput for *asset*.

    The frontend Deep Analysis Modal consumes this to render
    Confluence Bars and the LLM reasoning trace.
    """
    redis_keys = polaris_signal_redis_keys(asset)
    raw: bytes | None = None
    for redis_key in redis_keys:
        raw = await redis.get(redis_key)
        if raw is not None:
            break

    if raw is None:
        logger.warning(
            "executive_analyze_cache_miss | asset={} | keys={}",
            asset,
            redis_keys,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no_cached_signal_for_{asset}",
        )

    try:
        payload: dict[str, Any] = msgspec.json.decode(raw)
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        logger.error(
            "executive_analyze_decode_failed | asset={} | exc={}",
            asset,
            exc,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="signal_decode_failed",
        )

    return AnalyzeResponse(
        asset=str(payload.get("asset", asset)),
        signal=payload,
        fetched_at=datetime.now(timezone.utc),
    )
