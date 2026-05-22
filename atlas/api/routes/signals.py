from __future__ import annotations

import msgspec
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Literal, Optional

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from loguru import logger
from redis.asyncio import Redis

from atlas.api._channel_reads import (
    match_price_payload_for_asset,
    read_prices,
    read_scores,
)
from atlas.api.polaris_signals_redis_keys import (
    polaris_signal_redis_keys,
    polaris_signal_wire_tokens,
    signal_latest_redis_keys,
)
from atlas.api.schemas import (
    AgentBreakdownCellPublic,
    AssetInspectorPayload,
    ConfluenceScoreHistoryPoint,
    DerivativesFundingPoint,
    DerivativesOiPoint,
    ExchangeFlowDetail,
    FracDiffDetail,
    OBTIDetail,
    OptionsFlowDetail,
    RecentDecisionOutcome,
    ScoresPayload,
    SignalDetailPayload,
    SignalFeedEntry,
    SignalHistoryEntry,
)
from atlas.orchestrator.scorer import CATEGORY_WEIGHTS
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/signals", tags=["signals"])

_CONFLUENCE_TOTAL_WEIGHT = 220


def _signal_detail_category_max_scores() -> dict[str, float]:
    """Five-bar pillar ceilings on the raw 220 ladder (matches CATEGORY_WEIGHTS).

    The dashboard collapses exchange/on-chain/macro agents into Derivatives,
    On-chain, Technical, Sentiment, and Market context — not arbitrary /40 buckets.
    """

    weights = CATEGORY_WEIGHTS

    def pillar_raw(*cats: str) -> float:
        portion = float(_CONFLUENCE_TOTAL_WEIGHT) * sum(weights[c] for c in cats)
        return round(portion, 6)

    return {
        "derivatives": pillar_raw("derivatives", "liquidation", "funding"),
        "onchain": pillar_raw("onchain", "whale"),
        "technical": pillar_raw("technical"),
        "sentiment": pillar_raw("sentiment"),
        "marketContext": pillar_raw("regime", "correlation", "macro", "news_macro"),
    }


async def _redis_get_signal_dict(redis: Redis, asset_query: str) -> dict[str, Any]:
    """Load newest cached ``SignalOutput`` JSON (``polaris:signals:*`` then ``signal:latest:*``)."""
    lookup_keys = [
        *polaris_signal_redis_keys(asset_query),
        *signal_latest_redis_keys(asset_query),
    ]
    if not lookup_keys:
        return {}
    blobs = await redis.mget(lookup_keys)
    for raw in blobs:
        if not raw:
            continue
        try:
            decoded = msgspec.json.decode(raw)
        except Exception:
            continue
        if isinstance(decoded, dict):
            return decoded
    return {}


def _safe_float(value: object, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalize_obti_side(raw: object) -> str | None:
    if raw is None:
        return None
    token = str(raw).lower().strip()
    return token if token in ("bid", "ask") else None


def _signal_json_float(
    data: dict[str, Any],
    snake: str,
    camel: str | None = None,
) -> float | None:
    """Read a numeric field from snake_case or optional camelCase Redis/JSON keys."""
    val = data.get(snake)
    if val is None and camel is not None:
        val = data.get(camel)
    if val is None:
        return None
    return _safe_float(val, 0.0)


def _raw_confluence_score(data: dict[str, Any]) -> int:
    """Mirror ``_scores_payload_from_signal`` — default raw=0 must not mask real ``score``.

    ``SignalOutput`` often serialises ``raw_confluence_score: 0`` even when the ladder
    score lives in ``score`` (0–100); the websocket path multiplies by 220/100.
    """

    raw_opt = _signal_json_float(data, "raw_confluence_score", "rawConfluenceScore")
    raw_int = int(round(raw_opt)) if raw_opt is not None else 0
    if raw_int > 0:
        return max(0, min(_CONFLUENCE_TOTAL_WEIGHT, raw_int))

    normalized = _signal_json_float(data, "score")
    if normalized is None:
        normalized = _signal_json_float(data, "normalized_score", "normalizedScore")
    norm_f = normalized if normalized is not None else 0.0
    derived = int(round(norm_f / 100.0 * float(_CONFLUENCE_TOTAL_WEIGHT)))
    return max(0, min(_CONFLUENCE_TOTAL_WEIGHT, derived))


def _normalized_score_int(data: dict[str, Any], raw_220: int) -> int:
    s = _signal_json_float(data, "score")
    if s is not None:
        return int(round(max(0.0, min(100.0, s))))
    alt = _signal_json_float(data, "normalized_score", "normalizedScore")
    if alt is not None:
        return int(round(max(0.0, min(100.0, alt))))
    return min(100, max(0, int(round(raw_220 / 220.0 * 100))))


def _gate_threshold_value(data: dict[str, Any], settings: PolarisSettings) -> float:
    if data.get("gate_threshold") is not None:
        return _safe_float(data["gate_threshold"], float(settings.router_gated_threshold))
    return float(settings.router_gated_threshold)


def _passes_conviction_gate(data: dict[str, Any], raw_220: int, gate: float) -> bool:
    explicit = data.get("passes_gate")
    if isinstance(explicit, bool):
        return explicit
    psm = data.get("position_size_modifier")
    if psm is not None and _safe_float(psm, 1.0) <= 0.0:
        return False
    return float(raw_220) >= gate


def _obti_feed_fields(data: dict[str, Any]) -> tuple[str | None, str | None]:
    obti = data.get("obti")
    if isinstance(obti, dict):
        level = str(obti.get("level", "balanced")).lower().strip()
        side_s = _normalize_obti_side(obti.get("side"))
        if level in ("balanced", "low", ""):
            return None, None
        if level == "moderate":
            return "moderate", side_s
        if level == "extreme":
            return "extreme", side_s

    flat_sum = data.get("obti_summary")
    if isinstance(flat_sum, str):
        token = flat_sum.lower().strip()
        if token in ("balanced", "low"):
            return None, None
        if token == "moderate":
            return "moderate", _normalize_obti_side(data.get("obti_side"))
        if token == "extreme":
            return "extreme", _normalize_obti_side(data.get("obti_side"))
    return None, None


def _llm_tier_from_signal(data: dict[str, Any]) -> str | None:
    label = data.get("llm_tier_label")
    if isinstance(label, str) and label.strip():
        return label.strip()
    return None


def _flatten_category_scores(cat_scores: object) -> dict[str, float]:
    if not isinstance(cat_scores, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in cat_scores.items():
        out[str(key)] = _safe_float(val, 0.0)
    return out


def _build_obti_detail(data: dict[str, Any]) -> OBTIDetail | None:
    obti = data.get("obti")
    if not isinstance(obti, dict) or not obti:
        return None
    level = str(obti.get("level", "balanced")).lower().strip() or "balanced"
    hist_raw = obti.get("history", [])
    history: list[float] = []
    if isinstance(hist_raw, list):
        for cell in hist_raw:
            history.append(_safe_float(cell, 0.0))
    return OBTIDetail(
        side=_normalize_obti_side(obti.get("side")),
        level=level,
        obti_bid=_safe_float(obti.get("bid", obti.get("obti_bid")), 0.0),
        obti_ask=_safe_float(obti.get("ask", obti.get("obti_ask")), 0.0),
        moderate_threshold=_safe_float(
            obti.get("moderate_threshold", obti.get("moderateThreshold")),
            0.0,
        ),
        extreme_threshold=_safe_float(
            obti.get("extreme_threshold", obti.get("extremeThreshold")),
            0.0,
        ),
        samples=int(round(_safe_float(obti.get("samples"), 0.0))),
        min_samples=max(
            1,
            int(round(_safe_float(obti.get("min_samples", obti.get("minSamples")), 30.0))),
        ),
        is_warming_up=bool(obti.get("is_warming_up", obti.get("isWarmingUp", False))),
        history=history,
        last_book_update_ms=int(
            round(
                _safe_float(
                    obti.get("last_book_update_ms", obti.get("lastBookUpdateMs")),
                    0.0,
                ),
            ),
        ),
    )


def _build_frac_diff(data: dict[str, Any]) -> FracDiffDetail | None:
    fd = data.get("frac_diff")
    if not isinstance(fd, dict):
        return None
    last_cal = fd.get("last_calibrated", fd.get("lastCalibrated"))
    last_cal_s = str(last_cal) if last_cal is not None else ""
    return FracDiffDetail(
        optimal_order=_safe_float(fd.get("optimal_order", fd.get("optimalOrder")), 0.0),
        memory_preserved=_safe_float(fd.get("memory_preserved", fd.get("memoryPreserved")), 0.0),
        adf_p_value=_safe_float(fd.get("adf_p_value", fd.get("adfPValue")), 1.0),
        is_stationary=bool(fd.get("is_stationary", fd.get("isStationary", False))),
        last_calibrated=last_cal_s,
    )


def _norm_str_dict(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    return {str(k): str(v) for k, v in raw.items()}


def _build_options_flow(data: dict[str, Any]) -> OptionsFlowDetail | None:
    of = data.get("options_flow")
    if not isinstance(of, dict):
        return None
    provider = _norm_str_dict(of.get("provider"))
    if provider is None:
        return None
    return OptionsFlowDetail(
        provider=provider,
        dvol=_optional_float(of.get("dvol")),
        atm_iv=_optional_float(of.get("atm_iv", of.get("atmIv"))),
        skew25d=_optional_float(of.get("skew25d", of.get("skew_25d"))),
        vrp=_optional_float(of.get("vrp")),
        call_put_oi_ratio=(
            str(of["call_put_oi_ratio"])
            if of.get("call_put_oi_ratio") is not None
            else (
                str(of["callPutOiRatio"])
                if of.get("callPutOiRatio") is not None
                else None
            )
        ),
        regime=str(of["regime"]) if of.get("regime") is not None else None,
        unusual_activity=(
            str(of["unusual_activity"])
            if of.get("unusual_activity") is not None
            else (
                str(of["unusualActivity"]) if of.get("unusualActivity") is not None else None
            )
        ),
    )


def _build_exchange_flow(data: dict[str, Any]) -> ExchangeFlowDetail | None:
    ef = data.get("exchange_flow")
    if not isinstance(ef, dict):
        return None
    provider = _norm_str_dict(ef.get("provider"))
    if provider is None:
        return None
    nf = ef.get("net_flow_24h", ef.get("netFlow24h"))
    ftrend = ef.get("flow_trend", ef.get("flowTrend"))
    sres = ef.get("stablecoin_reserves", ef.get("stablecoinReserves"))
    sig = ef.get("signal")

    return ExchangeFlowDetail(
        provider=provider,
        net_flow_24h=str(nf) if nf is not None else None,
        flow_z_score=_optional_float(ef.get("flow_z_score", ef.get("flowZScore"))),
        flow_trend=str(ftrend) if ftrend is not None else None,
        stablecoin_reserves=str(sres) if sres is not None else None,
        signal=str(sig) if sig is not None else None,
    )


def _jsonable_inspector_value(value: object) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _jsonable_inspector_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable_inspector_value(v) for v in value]
    return str(value)


def _coerce_sub_signal_map(raw: object) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for key, cell in raw.items():
        name = str(key)
        if isinstance(cell, dict):
            out[name] = {
                str(nested_k): _jsonable_inspector_value(nested_v)
                for nested_k, nested_v in cell.items()
            }
        else:
            out[name] = {"value": str(cell), "flag": "INFO"}
    return out


def _parse_agent_breakdown_public(data: dict[str, Any]) -> dict[str, AgentBreakdownCellPublic]:
    raw = data.get("agent_breakdown")
    if not isinstance(raw, dict):
        return {}
    cells: dict[str, AgentBreakdownCellPublic] = {}
    for agent_key, cell in raw.items():
        if not isinstance(cell, dict):
            continue
        direction_raw = cell.get("direction")
        direction_s: str | None
        if direction_raw is None:
            direction_s = None
        else:
            direction_s = str(direction_raw)
        conv_raw = cell.get("convergences")
        convergences: list[str] = []
        if isinstance(conv_raw, list):
            convergences = [str(x) for x in conv_raw]
        risks_raw = cell.get("risks")
        risks: list[str] = []
        if isinstance(risks_raw, list):
            risks = [str(x) for x in risks_raw]
        cells[str(agent_key)] = AgentBreakdownCellPublic(
            score=_safe_float(cell.get("score"), 0.0),
            max_score=_safe_float(cell.get("max_score", cell.get("maxScore")), 0.0),
            weight=_safe_float(cell.get("weight"), 1.0),
            direction=direction_s,
            explanation=str(cell.get("explanation", "")),
            convergences=convergences,
            risks=risks,
            veto=bool(cell.get("veto", False)),
            sub_signals=_coerce_sub_signal_map(cell.get("sub_signals")),
        )
    return cells


def _pillars_from_flat_category_scores(flat: dict[str, Any]) -> dict[str, float]:
    """Collapse granular ``category_scores`` into five executive pillars (matches dashboard)."""

    extracted = {str(k): _safe_float(v, 0.0) for k, v in flat.items()}
    derivatives = (
        extracted.get("derivatives", 0.0)
        + extracted.get("liquidation", 0.0)
        + extracted.get("funding", 0.0)
    )
    onchain = extracted.get("onchain", 0.0) + extracted.get("whale", 0.0)
    macro = (
        extracted.get("regime", 0.0)
        + extracted.get("correlation", 0.0)
        + extracted.get("news_macro", 0.0)
        + extracted.get("macro", 0.0)
        + extracted.get("context", 0.0)
    )
    market = extracted.get("marketContext", macro)
    return {
        "derivatives": round(derivatives, 4),
        "onchain": round(onchain, 4),
        "technical": round(extracted.get("technical", 0.0), 4),
        "sentiment": round(extracted.get("sentiment", 0.0), 4),
        "market_context": round(market, 4),
    }


def _category_scores_from_signal_history_metadata(meta_raw: object) -> dict[str, float] | None:
    if not isinstance(meta_raw, dict):
        return None
    inner = meta_raw.get("category_scores")
    if not isinstance(inner, dict):
        return None
    return {str(k): _safe_float(v, 0.0) for k, v in inner.items()}


async def _okx_derivatives_series(
    redis: Redis,
    wire_tokens: list[str],
) -> tuple[list[DerivativesFundingPoint], list[DerivativesOiPoint], Literal["okx_mcp_cache", "none"]]:
    from atlas.providers.okx_mcp import cache as okx_cache

    funding_hist = None
    oi_hist = None
    candidates = list(dict.fromkeys(wire_tokens))
    for tok in candidates:
        funding_hist = await okx_cache.read_funding_history(redis, tok)
        if funding_hist is not None and funding_hist.bars:
            break
    for tok in candidates:
        oi_hist = await okx_cache.read_oi_history(redis, tok)
        if oi_hist is not None and oi_hist.bars:
            break

    if funding_hist is None and oi_hist is None:
        return [], [], "none"

    funding_points: list[DerivativesFundingPoint] = []
    if funding_hist is not None:
        for bar in funding_hist.bars:
            funding_points.append(
                DerivativesFundingPoint(
                    funding_time_ms=int(bar.funding_time),
                    funding_rate=float(bar.funding_rate),
                ),
            )

    oi_points: list[DerivativesOiPoint] = []
    if oi_hist is not None:
        for bar in oi_hist.bars:
            oi_points.append(
                DerivativesOiPoint(
                    ts_ms=int(bar.ts),
                    oi_usd=float(bar.oi),
                ),
            )

    return funding_points, oi_points, "okx_mcp_cache"


@router.get("/latest", response_model=ScoresPayload, response_model_by_alias=True)
async def get_latest_signal(request: Request) -> ScoresPayload | Response:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()

    raw = await redis.get("polaris:latest_signal")
    if raw is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return await read_scores(redis, settings)


@router.get("/history", response_model=List[SignalHistoryEntry], response_model_by_alias=True)
async def get_signal_history(
    request: Request,
    limit: int = Query(50, ge=1, le=500),
    asset: Optional[str] = None,
) -> List[SignalHistoryEntry]:
    pool_raw = request.app.state.db_pool
    if pool_raw is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="historical_store_unavailable",
        )
    pool: asyncpg.Pool = pool_raw

    query = """
        SELECT id, asset, timestamp, total_score, decision, conviction, passes_gate
        FROM signals
    """
    args: list[Any] = []
    if asset:
        query += " WHERE asset = $1"
        args.append(asset)

    query += f" ORDER BY timestamp DESC LIMIT ${len(args) + 1}"
    args.append(limit)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)

    return [
        SignalHistoryEntry(
            id=row["id"],
            asset=row["asset"],
            timestamp=row["timestamp"].isoformat()
            if hasattr(row["timestamp"], "isoformat")
            else str(row["timestamp"]),
            total_score=float(row["total_score"]),
            decision=row["decision"],  # type: ignore[arg-type]
            conviction=float(row["conviction"]),
            passes_gate=bool(row["passes_gate"]),
        )
        for row in rows
    ]


@router.get("/feed", response_model=List[SignalFeedEntry], response_model_by_alias=True)
async def get_signal_feed(request: Request) -> List[SignalFeedEntry]:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()

    raw_syms = await redis.get(settings.ACTIVE_SYMBOLS_REDIS_KEY)
    sym_list: list[str] = []
    if raw_syms:
        if isinstance(raw_syms, bytes):
            raw_syms = raw_syms.decode("utf-8")
        try:
            sym_list = msgspec.json.decode(raw_syms, type=List[str])
        except Exception:
            sym_list = []
    if not sym_list:
        sym_list = ["BTC", "ETH", "SOL"]

    prices = await read_prices(redis, settings)

    entries: list[SignalFeedEntry] = []
    for asset in sym_list:
        data = await _redis_get_signal_dict(redis, asset)
        if not data:
            continue

        raw_220 = _raw_confluence_score(data)
        gate = _gate_threshold_value(data, settings)
        norm = _normalized_score_int(data, raw_220)
        obti_summary, obti_side = _obti_feed_fields(data)
        p = match_price_payload_for_asset(prices, asset)
        cycle_raw = data.get("cycle_number")

        entries.append(
            SignalFeedEntry(
                asset=asset,
                timestamp=str(data.get("timestamp", "")),
                decision=str(data.get("decision", "Hold")),
                total_score=float(raw_220),
                normalized_score=float(norm),
                confidence=_safe_float(data.get("confidence"), 0.0),
                passes_gate=_passes_conviction_gate(data, raw_220, gate),
                gate_threshold=gate,
                obti_summary=obti_summary,
                obti_side=obti_side,
                llm_tier_label=_llm_tier_from_signal(data),
                cycle_number=int(cycle_raw) if cycle_raw is not None else None,
                price=p.price if p else "0.0",
                change_24h=str(p.change_24h) if p else "0.0",
            ),
        )

    return entries


@router.get("/{asset}/inspector", response_model=AssetInspectorPayload, response_model_by_alias=True)
async def get_asset_inspector(
    request: Request,
    asset: str,
    days: int = Query(30, ge=1, le=90),
) -> AssetInspectorPayload:
    """Time series (``signal_history``), OKX derivatives cache, and recent outcomes."""

    redis: Redis = request.app.state.redis
    wire = polaris_signal_wire_tokens(asset)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    score_points: list[ConfluenceScoreHistoryPoint] = []
    recent: list[RecentDecisionOutcome] = []

    pool_raw = request.app.state.db_pool
    if pool_raw is not None:
        pool: asyncpg.Pool = pool_raw
        try:
            async with pool.acquire() as conn:
                hist_rows = await conn.fetch(
                    """
                    SELECT created_at, raw_score, score, decision, metadata
                    FROM signal_history
                    WHERE asset = ANY($1::text[]) AND created_at >= $2
                    ORDER BY created_at ASC
                    """,
                    wire,
                    cutoff,
                )
                for row in hist_rows:
                    ts = row["created_at"]
                    ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    meta_raw = row["metadata"]
                    flat = _category_scores_from_signal_history_metadata(meta_raw)
                    pillars = _pillars_from_flat_category_scores(flat) if flat is not None else {}
                    has_pillars = flat is not None
                    score_points.append(
                        ConfluenceScoreHistoryPoint(
                            timestamp=ts_s,
                            raw_score=float(row["raw_score"]),
                            normalized_score=float(row["score"]),
                            decision=str(row["decision"]),
                            derivatives=pillars.get("derivatives") if has_pillars else None,
                            onchain=pillars.get("onchain") if has_pillars else None,
                            technical=pillars.get("technical") if has_pillars else None,
                            sentiment=pillars.get("sentiment") if has_pillars else None,
                            market_context=pillars.get("market_context") if has_pillars else None,
                        ),
                    )

                out_rows = await conn.fetch(
                    """
                    SELECT signal_id, created_at, decision, raw_score, score,
                           outcome_label, pnl_pct
                    FROM signal_history
                    WHERE asset = ANY($1::text[])
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    wire,
                )
                for row in out_rows:
                    ts = row["created_at"]
                    ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
                    ol = row["outcome_label"]
                    pnl = row["pnl_pct"]
                    recent.append(
                        RecentDecisionOutcome(
                            signal_id=str(row["signal_id"]),
                            timestamp=ts_s,
                            decision=str(row["decision"]),
                            raw_score=float(row["raw_score"]),
                            normalized_score=float(row["score"]),
                            outcome_label=str(ol) if ol is not None else None,
                            pnl_pct=float(pnl) if pnl is not None else None,
                        ),
                    )
        except Exception as exc:
            logger.warning("asset_inspector_db_failed | asset={} | err={}", asset, str(exc))

    funding, oi, src = await _okx_derivatives_series(redis, wire)

    return AssetInspectorPayload(
        asset=asset,
        score_history=score_points,
        funding_history=funding,
        oi_history=oi,
        derivatives_source=src,
        recent_decisions=recent,
    )


@router.get("/{asset}", response_model=SignalDetailPayload, response_model_by_alias=True)
async def get_signal_detail(request: Request, asset: str) -> SignalDetailPayload:
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()

    data = await _redis_get_signal_dict(redis, asset)

    prices = await read_prices(redis, settings, symbols_query=asset)
    p = prices[0] if prices else None

    pool = request.app.state.db_pool
    history: list[SignalHistoryEntry] = []
    hist_assets: list[str] = []
    sig_asset = data.get("asset")
    if sig_asset:
        sig_s = str(sig_asset).strip()
        if sig_s:
            hist_assets.append(sig_s)
    hist_assets.extend(polaris_signal_wire_tokens(asset))
    hist_assets = list(dict.fromkeys([h for h in hist_assets if h]))
    if pool:
        query = (
            "SELECT id, asset, timestamp, total_score, decision, conviction, passes_gate "
            "FROM signals WHERE asset = ANY($1::text[]) ORDER BY timestamp DESC LIMIT 10"
        )
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, hist_assets)
                for row in rows:
                    history.append(
                        SignalHistoryEntry(
                            id=row["id"],
                            asset=row["asset"],
                            timestamp=row["timestamp"].isoformat()
                            if hasattr(row["timestamp"], "isoformat")
                            else str(row["timestamp"]),
                            total_score=float(row["total_score"]),
                            decision=row["decision"],  # type: ignore[arg-type]
                            conviction=float(row["conviction"]),
                            passes_gate=bool(row["passes_gate"]),
                        ),
                    )
        except Exception:
            pass

    cat_flat = _flatten_category_scores(data.get("category_scores", {}))
    raw_220 = _raw_confluence_score(data)
    gate = _gate_threshold_value(data, settings)
    norm = _normalized_score_int(data, raw_220)
    cycle_raw = data.get("cycle_number")

    obti_detail = _build_obti_detail(data)
    frac_diff = _build_frac_diff(data)
    options_flow = _build_options_flow(data)
    exchange_flow = _build_exchange_flow(data)
    agent_cells = _parse_agent_breakdown_public(data)

    return SignalDetailPayload(
        asset=asset,
        price=p.price if p else "0.0",
        change_24h=str(p.change_24h) if p else "0.0",
        cycle_number=int(cycle_raw) if cycle_raw is not None else 0,
        cycle_ts=str(data.get("timestamp", "")),
        total_score=float(raw_220),
        normalized_score=float(norm),
        decision=str(data.get("decision", "Hold")),
        confidence=_safe_float(data.get("confidence"), 0.0),
        passes_gate=_passes_conviction_gate(data, raw_220, gate),
        gate_threshold=gate,
        llm_tier_label=_llm_tier_from_signal(data),
        category_scores=cat_flat,
        category_max_scores=_signal_detail_category_max_scores(),
        agent_breakdown=agent_cells,
        obti_detail=obti_detail,
        frac_diff=frac_diff,
        options_flow=options_flow,
        exchange_flow=exchange_flow,
        signal_history=history,
    )
