from datetime import datetime, timezone
try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from typing import Any, Dict, List, Optional, Sequence
from redis.asyncio import Redis
from loguru import logger
import msgspec

from atlas.api.polaris_signals_redis_keys import polaris_signal_wire_tokens
from atlas.api.schemas import (
    AgentStatusPayload,
    AgentStatusRedis,
    ScoresPayload,
    PricePayload,
)
from atlas.models.signal import SignalOutput
from atlas.shared.config import PolarisSettings
from atlas.shared.hydra_asset import hydra_base_asset

try:
    from atlas.agents.registry import AGENT_CATEGORIES
except ImportError:
    AGENT_CATEGORIES = {}

_CONFLUENCE_AGENT_SPECS: dict[str, tuple[int, tuple[str, ...]]] = {
    "DerivativesAgent": (50, ("DerivativesAgent", "derivatives")),
    "WhaleWatcherAgent": (
        35,
        ("WhaleWatcherAgent", "whale", "onchain", "OnChainAgent"),
    ),
    "TechnicalAgent": (55, ("TechnicalAgent", "technical")),
    "SocialAgent": (
        40,
        ("SocialAgent", "sentiment", "sentiment_news_agent", "SentimentNewsAgent"),
    ),
    "MacroAgent": (
        40,
        ("MacroAgent", "news_macro_agent", "NewsMacroAgent", "regime"),
    ),
}

_CONFLUENCE_TOTAL_WEIGHT = 220

_AGENT_CATEGORY_FALLBACKS: Dict[str, str] = {
    "DerivativesAgent": "derivatives",
    "WhaleWatcherAgent": "onchain",
    "TechnicalAgent": "technical",
    "SocialAgent": "sentiment",
    "MacroAgent": "macro",
    "risk": "risk",
}

async def read_agents(redis: Redis) -> List[AgentStatusPayload]:
    agents: List[AgentStatusPayload] = []
    async for key in redis.scan_iter(match="agent:*:status"):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        raw = await redis.get(key)
        if raw is None:
            continue
        try:
            payload = AgentStatusRedis.model_validate_json(raw)
            name = key.split(":")[1]
            category = AGENT_CATEGORIES.get(name)
            if category is None:
                category = _AGENT_CATEGORY_FALLBACKS.get(name, "unknown")
                if category == "unknown":
                    logger.warning("agent_category_unknown | agent_name={}", name)
            
            status_val = payload.status
            if status_val not in ("GREEN", "YELLOW", "RED"):
                if status_val in ("READY", "WARMING_UP"):
                    status_val = "GREEN"
                elif status_val == "DEGRADED":
                    status_val = "YELLOW"
                elif status_val == "FAILED":
                    status_val = "RED"
                else:
                    status_val = "RED"
            
            agents.append(AgentStatusPayload(
                name=name,
                status=status_val,  # type: ignore
                last_ping_ms=payload.last_ping_ms,
                category=category,
                last_score=payload.last_score,
                max_points=payload.max_points,
                direction=payload.direction,
                explanation=payload.explanation,
            ))
        except Exception as exc:
            logger.error("failed_to_parse_agent_status | agent_key={} | err={}", key, str(exc))
    
    agents.sort(key=lambda a: a.name)
    return agents


def _scores_payload_empty_waiting() -> ScoresPayload:
    """Empty scores row when Redis has no authoritative signal."""

    now_iso = datetime.now(UTC).isoformat()
    return ScoresPayload(
        total_score=0,
        normalized_score=0,
        category_scores={},
        confidence=0.0,
        gate_threshold=0,
        passes_gate=False,
        is_stale=True,
        timestamp=now_iso,
        asset="",
        decision="",
        reasoning_summary="",
        cycle_timestamp=now_iso,
    )


def _scores_payload_from_signal(signal: SignalOutput, settings: PolarisSettings) -> ScoresPayload:
    """Build websocket / REST snapshot from a validated ``SignalOutput``."""

    ts = signal.timestamp
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    age_s = (datetime.now(UTC) - ts).total_seconds()
    is_stale = age_s > settings.SIGNAL_STALE_AFTER_SECONDS

    cat_scores: Dict[str, int] = {}
    if hasattr(signal, "category_scores") and signal.category_scores:
        cs = signal.category_scores
        cat_scores = {
            "technical": int(round(float(cs.technical))),
            "derivatives": int(round(float(cs.derivatives))),
            "onchain": int(round(float(cs.onchain))),
            "sentiment": int(round(float(cs.sentiment))),
            "whale": int(round(float(cs.whale))),
            "liquidation": int(round(float(cs.liquidation))),
            "regime": int(round(float(cs.regime))),
            "funding": int(round(float(cs.funding))),
            "news_macro": int(round(float(cs.news_macro))),
            "correlation": int(round(float(cs.correlation))),
            "context": int(round(float(cs.context))),
            "total": int(round(float(cs.total))),
        }

    asset_str = str(getattr(signal, "asset", "") or "").strip()

    decision_str = signal.decision.value
    reasoning_str = str(getattr(signal, "reasoning_summary", "") or "")
    raw_score = int(round(float(getattr(signal, "raw_confluence_score", 0) or 0)))
    if raw_score <= 0:
        raw_score = int(round(float(signal.score) / 100.0 * _CONFLUENCE_TOTAL_WEIGHT))

    passes_gate = float(signal.position_size_modifier) > 0.0 and float(raw_score) >= float(
        settings.router_gated_threshold
    )

    normalized_score = int(round(float(signal.score))) if hasattr(signal, "score") else 0
    cycle_number = getattr(getattr(signal, "telemetry", None), "cycle_number", None)
    llm_label = ""
    if getattr(signal, "deepseek_evaluation", None) is not None:
        llm_label = "DeepSeek"

    return ScoresPayload(
        total_score=max(0, min(_CONFLUENCE_TOTAL_WEIGHT, raw_score)),
        normalized_score=max(0, min(100, normalized_score)),
        category_scores=cat_scores,
        confidence=float(signal.confidence) if hasattr(signal, "confidence") else 0.0,
        gate_threshold=int(round(float(settings.router_gated_threshold))),
        passes_gate=passes_gate,
        is_stale=is_stale,
        timestamp=ts.isoformat(),
        asset=asset_str,
        decision=decision_str,
        reasoning_summary=reasoning_str,
        llm_tier_label=llm_label,
        cycle_number=cycle_number if isinstance(cycle_number, int) else None,
        cycle_timestamp=ts.isoformat(),
    )


async def read_scores(redis: Redis, settings: PolarisSettings) -> ScoresPayload:
    """Latest global signal from ``polaris:latest_signal`` (REST default)."""

    raw = await redis.get("polaris:latest_signal")
    if raw is None:
        return _scores_payload_empty_waiting()

    signal = SignalOutput.model_validate_json(raw)
    return _scores_payload_from_signal(signal, settings)


async def read_scores_ws_broadcast(redis: Redis, settings: PolarisSettings) -> list[ScoresPayload]:
    """Snapshots for dashboard asset selection — one row per Redis ``polaris:signals:*``.

    Consumers may receive multiple payloads per tick so the frontend can hydrate
    per-asset ladders without falsely reusing BTC when ETH/SOL snapshots exist.
    """
    newest_by_asset: dict[str, tuple[float, ScoresPayload]] = {}

    raw_latest = await redis.get("polaris:latest_signal")
    if raw_latest is not None:
        try:
            sig = SignalOutput.model_validate_json(raw_latest)
            if sig.asset.strip():
                newest_by_asset[sig.asset.strip().upper()] = (
                    _signal_epoch_seconds(sig),
                    _scores_payload_from_signal(sig, settings),
                )
        except Exception as exc:
            logger.warning(
                "read_scores_ws_broadcast | latest_decode_failed | err={}",
                str(exc),
            )

    try:
        async for rkey in redis.scan_iter(match="polaris:signals:*"):
            raw = await redis.get(rkey)
            if raw is None:
                continue
            try:
                sig = SignalOutput.model_validate_json(raw)
                if not sig.asset.strip():
                    continue
                bucket = sig.asset.strip().upper()
                epoch = _signal_epoch_seconds(sig)
                prev = newest_by_asset.get(bucket)
                if prev is None or epoch >= prev[0]:
                    newest_by_asset[bucket] = (epoch, _scores_payload_from_signal(sig, settings))
            except Exception as exc:
                key_text = (
                    rkey.decode("utf-8", errors="replace")
                    if isinstance(rkey, (bytes, bytearray))
                    else str(rkey)
                )
                logger.warning(
                    "read_scores_ws_broadcast | per_asset_decode_failed | key={} | err={}",
                    key_text,
                    str(exc),
                )
    except Exception as exc:
        logger.warning(
            "read_scores_ws_broadcast | scan_failed | err={}",
            str(exc),
        )

    if not newest_by_asset:
        return [_scores_payload_empty_waiting()]

    payloads = [pair[1] for pair in newest_by_asset.values()]
    payloads.sort(key=lambda payload: payload.asset.upper())
    return payloads


def _signal_epoch_seconds(signal: SignalOutput) -> float:
    """UTC epoch tie-break when deduplicating snapshots for the same asset."""

    ts = signal.timestamp
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)

    return float(ts.timestamp())


async def read_confluence(redis: Redis) -> Dict[str, Any]:
    """Build the frontend confluence dashboard frame from cached signals."""
    raw = await redis.get("polaris:latest_signal")
    if raw is None:
        raw = await _read_newest_asset_signal(redis)
    if raw is None:
        return _empty_confluence_frame()

    try:
        signal: Dict[str, Any] = msgspec.json.decode(raw)
    except Exception as exc:
        logger.warning("confluence_frame_decode_failed | err={}", exc)
        return _empty_confluence_frame()

    agents = _build_confluence_agents(signal)
    return {
        "asset": str(signal.get("asset", "UNKNOWN")),
        "timestamp": _coerce_epoch_timestamp(signal.get("timestamp")),
        "total_score": _calculate_confluence_total(signal, agents),
        "agents": agents,
    }


async def _read_newest_asset_signal(redis: Redis) -> bytes | None:
    """Return the freshest per-asset signal payload when latest is absent."""
    newest_raw: bytes | None = None
    newest_timestamp = 0.0
    async for key in redis.scan_iter(match="polaris:signals:*"):
        raw = await redis.get(key)
        if raw is None:
            continue
        try:
            payload: Dict[str, Any] = msgspec.json.decode(raw)
        except Exception:
            continue
        timestamp = _coerce_epoch_timestamp(payload.get("timestamp"))
        if timestamp >= newest_timestamp:
            newest_raw = raw
            newest_timestamp = timestamp
    return newest_raw


def _empty_confluence_frame() -> Dict[str, Any]:
    agents = {
        name: {"score": 0.0, "weight": weight, "sub_signals": {}}
        for name, (weight, _aliases) in _CONFLUENCE_AGENT_SPECS.items()
    }
    return {
        "asset": "WAITING",
        "timestamp": datetime.now(UTC).timestamp(),
        "total_score": 0.0,
        "agents": agents,
    }


def _build_confluence_agents(signal: Dict[str, Any]) -> Dict[str, Any]:
    agent_breakdown = signal.get("agent_breakdown", {})
    category_scores = signal.get("category_scores", {})
    if not isinstance(agent_breakdown, dict):
        agent_breakdown = {}
    if not isinstance(category_scores, dict):
        category_scores = {}

    agents: Dict[str, Any] = {}
    for agent_name, (weight, aliases) in _CONFLUENCE_AGENT_SPECS.items():
        agent_payload = _find_agent_payload(agent_breakdown, aliases)
        agents[agent_name] = _build_confluence_agent(
            agent_payload,
            category_scores,
            aliases,
            weight,
        )
    return agents


def _find_agent_payload(
    agent_breakdown: Dict[str, Any],
    aliases: tuple[str, ...],
) -> Dict[str, Any] | None:
    for alias in aliases:
        payload = agent_breakdown.get(alias)
        if isinstance(payload, dict):
            return payload
    return None


def _build_confluence_agent(
    agent_payload: Dict[str, Any] | None,
    category_scores: Dict[str, Any],
    aliases: tuple[str, ...],
    weight: int,
) -> Dict[str, Any]:
    if agent_payload is not None:
        score = _calculate_agent_percent(agent_payload)
        sub_signals = _normalise_sub_signals(agent_payload.get("sub_signals", {}))
    else:
        score = _calculate_category_percent(category_scores, aliases)
        sub_signals = {}
    return {"score": score, "weight": weight, "sub_signals": sub_signals}


def _calculate_agent_percent(agent_payload: Dict[str, Any]) -> float:
    raw_score = _coerce_float(agent_payload.get("score"), 0.0)
    max_score = _coerce_float(agent_payload.get("max_score"), 100.0)
    if max_score <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (raw_score / max_score) * 100.0)), 2)


def _calculate_category_percent(
    category_scores: Dict[str, Any],
    aliases: tuple[str, ...],
) -> float:
    values = [
        _coerce_float(category_scores.get(alias), 0.0)
        for alias in aliases
        if alias in category_scores
    ]
    if not values:
        return 0.0
    return round(max(0.0, min(100.0, max(values))), 2)


def _calculate_confluence_total(
    signal: Dict[str, Any],
    agents: Dict[str, Any],
) -> float:
    raw_score = _coerce_float(signal.get("raw_confluence_score"), 0.0)
    if raw_score > 0:
        return round(max(0.0, min(float(_CONFLUENCE_TOTAL_WEIGHT), raw_score)), 2)
    total = sum(
        _coerce_float(agent["score"], 0.0) / 100.0 * _coerce_float(agent["weight"], 0.0)
        for agent in agents.values()
    )
    return round(max(0.0, min(float(_CONFLUENCE_TOTAL_WEIGHT), total)), 2)


def _normalise_sub_signals(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for key, raw_signal in value.items():
        if isinstance(raw_signal, dict):
            result[str(key)] = {
                "value": raw_signal.get("value", ""),
                "flag": str(raw_signal.get("flag", "INFO")),
            }
        else:
            result[str(key)] = {"value": str(raw_signal), "flag": "INFO"}
    return result


def _coerce_epoch_timestamp(value: Any) -> float:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        normalised = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalised).timestamp()
        except ValueError:
            return datetime.now(UTC).timestamp()
    return datetime.now(UTC).timestamp()


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


from atlas.shared.coingecko_symbol_map import COINGECKO_SIMPLE_PRICE_ID_BY_BASE


def _base_symbol_from_wire_tokens(tokens: Sequence[str]) -> Optional[str]:
    """Derive a single base symbol (e.g. ``BTC``) from wire token aliases."""
    if not tokens:
        return None
    first = tokens[0].strip().upper()
    if "/" in first:
        base, _, quote = first.partition("/")
        if base:
            return base
    if first.endswith("USDT") and len(first) > 4:
        return first[:-4]
    return first


def _iso_utc_from_epoch_seconds(epoch: Any) -> str:
    if isinstance(epoch, int | float) and epoch > 0:
        return datetime.fromtimestamp(float(epoch), tz=UTC).isoformat().replace("+00:00", "Z")
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _price_payload_from_pyth_dict(base: str, data: Dict[str, Any]) -> Optional[PricePayload]:
    """Build ``PricePayload`` from ``atlas:price:{ASSET}`` (Pyth Hermes) cache."""
    raw_price = data.get("price")
    if raw_price is None or str(raw_price).strip() == "":
        return None
    publish_raw = data.get("publish_time")
    return PricePayload(
        symbol=base,
        price=str(raw_price).strip(),
        change_24h=0.0,
        volume_24h="0",
        timestamp=_iso_utc_from_epoch_seconds(publish_raw),
    )


def _price_payload_from_coingecko_dict(base: str, data: Dict[str, Any]) -> Optional[PricePayload]:
    """Build ``PricePayload`` from ``provider:coingecko:price:{coin_id}`` cache."""
    raw_price = data.get("price_usd")
    if raw_price is None or str(raw_price).strip() in {"", "0"}:
        return None
    vol = data.get("volume_24h", "0")
    ts = data.get("last_updated_utc")
    if not isinstance(ts, str) or not ts.strip():
        ts = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return PricePayload(
        symbol=base,
        price=str(raw_price).strip(),
        change_24h=0.0,
        volume_24h=str(vol).strip() if str(vol).strip() else "0",
        timestamp=ts.strip(),
    )


def _unique_base_symbols_from_tokens(tokens: Sequence[str]) -> list[str]:
    """Ordered unique bases (e.g. ``RNDR`` then ``RENDER``) for Pyth / CoinGecko keys."""
    seen: set[str] = set()
    ordered: list[str] = []
    for tok in tokens:
        base = _base_symbol_from_wire_tokens([tok])
        if base and base not in seen:
            seen.add(base)
            ordered.append(base)
    return ordered


async def _fallback_spot_price_from_redis(
    redis: Redis,
    tokens: list[str],
) -> Optional[PricePayload]:
    """Use Pyth (``atlas:price``) or CoinGecko cache when Bitget rows are missing."""
    for base in _unique_base_symbols_from_tokens(tokens):
        pyth_key = f"atlas:price:{base}"
        try:
            raw_pyth = await redis.get(pyth_key)
            if raw_pyth:
                decoded: Any = msgspec.json.decode(raw_pyth)
                if isinstance(decoded, dict):
                    row = _price_payload_from_pyth_dict(base, decoded)
                    if row is not None:
                        return row
        except Exception as exc:
            logger.warning(
                "fallback_price_pyth_failed | base={} | err={}",
                base,
                str(exc),
            )

    for base in _unique_base_symbols_from_tokens(tokens):
        gecko_id = COINGECKO_SIMPLE_PRICE_ID_BY_BASE.get(base)
        if not gecko_id:
            continue
        cg_key = f"provider:coingecko:price:{gecko_id}"
        try:
            raw_cg = await redis.get(cg_key)
            if raw_cg:
                decoded_cg: Any = msgspec.json.decode(raw_cg)
                if isinstance(decoded_cg, dict):
                    row = _price_payload_from_coingecko_dict(base, decoded_cg)
                    if row is not None:
                        return row
        except Exception as exc:
            logger.warning(
                "fallback_price_coingecko_failed | base={} | err={}",
                base,
                str(exc),
            )
    return None


def match_price_payload_for_asset(
    prices: Sequence[PricePayload],
    asset_query: str,
) -> Optional[PricePayload]:
    """Match a Bitget price row when ACTIVE_SYMBOLS uses a legacy slug (e.g. RNDR vs RENDER)."""
    pmap = {p.symbol.upper(): p for p in prices}
    for tok in polaris_signal_wire_tokens(asset_query):
        hit = pmap.get(tok.upper())
        if hit is not None:
            return hit
    return None


async def _read_redis_spot_price_for_wire_query(
    redis: Redis,
    asset_query: str,
) -> Optional[PricePayload]:
    """Resolve spot row: Pyth ``atlas:price`` / CoinGecko cache first, then Bitget.

    Bitget rows may be missing or very stale when only the Hermes connector runs;
    prefer oracle / cross-reference caches so OmniBox does not surface dead prices.
    """
    tokens = polaris_signal_wire_tokens(asset_query)
    from_oracle = await _fallback_spot_price_from_redis(redis, tokens)
    if from_oracle is not None:
        return from_oracle

    keys = [f"provider:bitget:price:{t}" for t in tokens]
    if not keys:
        return None
    blobs = await redis.mget(keys)
    for token, raw in zip(tokens, blobs):
        if not raw:
            continue
        try:
            data: Dict[str, Any] = msgspec.json.decode(raw)
            if not isinstance(data, dict):
                continue
            if "symbol" not in data:
                data["symbol"] = token
            return PricePayload.model_validate(data)
        except Exception as exc:
            logger.warning("failed_to_parse_price | symbol={} | err={}", token, str(exc))
    return None


async def read_prices(redis: Redis, settings: PolarisSettings, symbols_query: Optional[str] = None) -> List[PricePayload]:
    if symbols_query:
        bundled: List[PricePayload] = []
        for q in (s.strip() for s in symbols_query.split(",") if s.strip()):
            row = await _read_redis_spot_price_for_wire_query(redis, q)
            if row is not None:
                bundled.append(row)
        return bundled

    raw_syms = await redis.get(settings.ACTIVE_SYMBOLS_REDIS_KEY)
    sym_list: List[str] = []
    if raw_syms:
        if isinstance(raw_syms, bytes):
            raw_syms = raw_syms.decode("utf-8")
        try:
            sym_list = msgspec.json.decode(raw_syms, type=List[str])
        except Exception as e:
            logger.warning("failed_to_decode_active_symbols | error={}", e)
    if not sym_list:
        sym_list = ["BTC", "ETH", "SOL"]

    prices: List[PricePayload] = []
    for symbol in sym_list:
        row = await _read_redis_spot_price_for_wire_query(redis, symbol)
        if row is not None:
            prices.append(row)
    return prices


def _find_regime_agent_cell(agent_breakdown: Any) -> Optional[Dict[str, Any]]:
    """Locate the market-regime agent entry inside a SignalOutput agent_breakdown dict."""
    if not isinstance(agent_breakdown, dict):
        return None
    for key in ("regime", "MRA"):
        cell = agent_breakdown.get(key)
        if isinstance(cell, dict) and isinstance(cell.get("sub_signals"), dict):
            return cell
    for cell in agent_breakdown.values():
        if not isinstance(cell, dict):
            continue
        if str(cell.get("agent_name", "")).lower() != "regime":
            continue
        if isinstance(cell.get("sub_signals"), dict):
            return cell
    return None


def _runner_up_hmm_label(probs: Any, current_native: Optional[str]) -> Optional[str]:
    """Return a short runner-up label from HMM regime probabilities (excluding current)."""
    if not isinstance(probs, dict) or not probs:
        return None
    ranked: list[tuple[str, float]] = []
    for raw_k, raw_v in probs.items():
        if not isinstance(raw_v, (int, float)):
            continue
        ranked.append((str(raw_k), float(raw_v)))
    if len(ranked) < 2:
        return None
    ranked.sort(key=lambda item: item[1], reverse=True)
    current_l = (current_native or "").lower()
    for name, mass in ranked:
        if name.lower() == current_l:
            continue
        pct = round(mass * 100.0) if mass <= 1.0 else round(mass)
        reader = "CHOP" if name.lower() == "volatile" else name.upper()
        return f"{reader} next at ~{pct}% HMM mass"
    return None


def _regime_context_bundle(
    sig: Dict[str, Any],
    regime_confidence_existing: float,
) -> tuple[Dict[str, Any], float]:
    """Build optional camelCase /ws/system regime context fields plus merged confidence (0–100)."""
    wire: Dict[str, Any] = {}
    confidence_out = regime_confidence_existing

    asset_raw = sig.get("asset")
    if isinstance(asset_raw, str) and asset_raw.strip():
        wire["regimeContextAsset"] = hydra_base_asset(asset_raw)

    tf_raw = sig.get("timeframe")
    if isinstance(tf_raw, str) and tf_raw.strip():
        wire["regimeContextTimeframe"] = tf_raw.strip()

    ts_raw = sig.get("timestamp")
    if isinstance(ts_raw, str) and ts_raw.strip():
        wire["regimeContextAsOf"] = ts_raw.strip()

    agent_blob = sig.get("agent_breakdown")
    if not isinstance(agent_blob, dict):
        agent_blob = sig.get("agentBreakdown")
    cell = _find_regime_agent_cell(agent_blob)

    native_current: Optional[str] = None
    if cell:
        subs = cell.get("sub_signals")
        if isinstance(subs, dict):
            duration = subs.get("duration")
            if isinstance(duration, int) and duration >= 0:
                wire["regimeContextDurationBars"] = duration

            probs = subs.get("probabilities")
            native_raw = subs.get("regime")
            if isinstance(native_raw, str):
                native_current = native_raw
            runner = _runner_up_hmm_label(probs, native_current)
            if runner:
                wire["regimeContextRunnerUp"] = runner

            if regime_confidence_existing <= 0.0 and isinstance(probs, dict) and isinstance(
                native_current,
                str,
            ):
                p_mass = probs.get(native_current)
                if p_mass is None:
                    p_mass = probs.get(native_current.lower())
                if isinstance(p_mass, (int, float)) and p_mass >= 0.0:
                    confidence_out = float(p_mass) * 100.0 if p_mass <= 1.0 else float(p_mass)

        expl = cell.get("explanation")
        if isinstance(expl, str) and expl.strip():
            wire["regimeContextExplanation"] = expl.strip()[:280]

    if confidence_out <= 0.0:
        try:
            c_sig = float(sig.get("confidence", 0.0))
        except (TypeError, ValueError):
            c_sig = 0.0
        if c_sig > 0.0:
            confidence_out = c_sig * 100.0 if c_sig <= 1.0 else c_sig

    wire["regimeContextTransitionHint"] = (
        "HMM on returns, realized vol, and volume for this pair/timeframe: "
        "the label tracks whichever state has the highest probability on the latest bar, "
        "and changes when that ordering shifts materially."
    )

    return wire, confidence_out


async def read_system(redis: Redis, settings: PolarisSettings) -> Dict[str, Any]:
    """Build SystemHealth payload for /ws/system channel.

    Synthesises from polaris:latest_signal, agent status keys,
    and price cache.  Returns shape expected by useSystemStore.
    """
    import time

    # Current regime from latest signal
    regime = "UNKNOWN"
    regime_confidence = 0.0
    regime_context: Dict[str, Any] = {}
    sig_dict: Optional[Dict[str, Any]] = None
    raw_signal = await redis.get("polaris:latest_signal")
    if raw_signal:
        try:
            decoded = msgspec.json.decode(raw_signal)
            if isinstance(decoded, dict):
                sig_dict = decoded
                regime = sig_dict.get("regime", sig_dict.get("market_regime", "UNKNOWN"))
                rc_raw = float(sig_dict.get("regime_confidence", 0.0))
                if 0.0 < rc_raw <= 1.0:
                    regime_confidence = rc_raw * 100.0
                else:
                    regime_confidence = rc_raw
        except Exception:
            sig_dict = None

    if sig_dict is not None:
        extra_ctx, regime_confidence = _regime_context_bundle(sig_dict, regime_confidence)
        regime_context = extra_ctx

    # Agent health → derive overall status
    agent_count = 0
    degraded_count = 0
    async for key in redis.scan_iter(match="agent:*:status"):
        agent_count += 1
        raw = await redis.get(key)
        if raw:
            try:
                data = msgspec.json.decode(raw)
                st = data.get("status", "RED")
                if st in ("DEGRADED", "YELLOW", "FAILED", "RED"):
                    degraded_count += 1
            except Exception:
                pass

    if agent_count == 0:
        overall_status = "DEGRADED"
    elif degraded_count > agent_count // 2:
        overall_status = "DEGRADED"
    else:
        overall_status = "HEALTHY"

    # Cycle info from Redis counters (best-effort)
    cycle_count_raw = await redis.get("polaris:cycle_count")
    if cycle_count_raw:
        try:
            cycle_text = (
                cycle_count_raw.decode("utf-8")
                if isinstance(cycle_count_raw, (bytes, bytearray))
                else str(cycle_count_raw)
            )
            cycle_count = int(cycle_text)
        except Exception:
            cycle_count = 0
    else:
        cycle_count = 0

    next_cycle_raw = await redis.get("polaris:next_cycle_ts")
    if next_cycle_raw:
        try:
            raw_text = (
                next_cycle_raw.decode("utf-8")
                if isinstance(next_cycle_raw, (bytes, bytearray))
                else str(next_cycle_raw)
            )
            next_ts = float(raw_text)
            next_cycle_seconds = max(0, int(next_ts - time.time()))
        except Exception:
            next_cycle_seconds = 0
    else:
        next_cycle_seconds = 0

    # Open positions
    open_positions = 0
    pos_raw = await redis.get("prometheus:positions")
    if pos_raw:
        try:
            pos_list = msgspec.json.decode(pos_raw)
            if isinstance(pos_list, list):
                open_positions = len(pos_list)
        except Exception:
            pass

    # BTC price
    btc_raw = await redis.get("provider:bitget:price:BTC")
    btc_price = "0"
    btc_change = "0"
    if btc_raw:
        try:
            btc_data = msgspec.json.decode(btc_raw)
            btc_price = str(btc_data.get("price", "0"))
            btc_change = str(btc_data.get("change_24h", "0"))
        except Exception:
            pass

    # PnL (best-effort)
    pnl_raw = await redis.get("prometheus:portfolio:pnl_24h")
    portfolio_pnl = str(pnl_raw.decode() if isinstance(pnl_raw, bytes) else pnl_raw) if pnl_raw else "0"

    payload: Dict[str, Any] = {
        "overallStatus": overall_status,
        "currentRegime": regime.upper() if isinstance(regime, str) else "UNKNOWN",
        "regimeConfidence": regime_confidence,
        "activeCycle": next_cycle_seconds > 0,
        "nextCycleSeconds": next_cycle_seconds,
        "cycleCount": cycle_count,
        "openPositions": open_positions,
        "portfolioPnl24h": portfolio_pnl,
        "btcPrice": btc_price,
        "btcChange24h": btc_change,
    }
    payload.update(regime_context)
    return payload


async def read_tactical(redis: Redis, settings: PolarisSettings) -> Dict[str, Any]:
    """Build multi-slice tactical payload for /ws/tactical channel.

    Returns a list of typed events that useTacticalChannel fans out:
      { type: 'regime',       data: RegimeSnapshot }
      { type: 'orderFlow',    data: OrderFlowSnapshot }
      { type: 'heatmap',      data: LiquidationCluster[] }
      { type: 'correlations', data: CorrelationRow[] }
    """
    events = []

    # ── Regime ──
    raw_signal = await redis.get("polaris:latest_signal")
    regime_kind = "RANGING"
    regime_confidence = 0.0
    regime_ts = datetime.now(UTC).isoformat()
    if raw_signal:
        try:
            sig = msgspec.json.decode(raw_signal)
            raw_regime = sig.get("regime", sig.get("market_regime", "ranging"))
            regime_map = {
                "bull": "TRENDING_UP", "bear": "TRENDING_DOWN",
                "volatile": "VOLATILE", "ranging": "RANGING",
                "breakout": "BREAKOUT", "breakdown": "BREAKDOWN",
            }
            regime_kind = regime_map.get(str(raw_regime).lower(), "RANGING")
            regime_confidence = float(sig.get("regime_confidence", sig.get("confidence", 0.5)))
            regime_ts = sig.get("timestamp", regime_ts)
        except Exception:
            pass

    events.append({
        "type": "regime",
        "data": {
            "kind": regime_kind,
            "confidence": regime_confidence,
            "changedAt": regime_ts,
            "cyclesSinceChange": 0,
            "previousKind": None,
        },
    })

    # ── Order Flow (OBTI) ──
    sym_list = ["BTC", "ETH", "SOL"]
    raw_syms = await redis.get(settings.ACTIVE_SYMBOLS_REDIS_KEY)
    if raw_syms:
        try:
            if isinstance(raw_syms, bytes):
                raw_syms = raw_syms.decode("utf-8")
            decoded = msgspec.json.decode(raw_syms, type=List[str])
            if decoded:
                sym_list = decoded
        except Exception:
            pass

    for sym in sym_list[:3]:
        obti_raw = await redis.get(f"polaris:signals:{sym}")
        if not obti_raw:
            continue
        try:
            sig_data = msgspec.json.decode(obti_raw)
            obti = sig_data.get("obti", {})
            if obti:
                events.append({
                    "type": "orderFlow",
                    "data": {
                        "asset": sym,
                        "obtiBid": float(obti.get("bid", 0.0)),
                        "obtiAsk": float(obti.get("ask", 0.0)),
                        "level": obti.get("level", "balanced"),
                        "side": obti.get("side"),
                        "isWarmingUp": bool(obti.get("is_warming_up", False)),
                        "samples": int(obti.get("samples", 0)),
                        "minSamples": int(obti.get("min_samples", 30)),
                        "updatedAt": datetime.now(UTC).isoformat(),
                    },
                })
                break  # send primary asset only per push
        except Exception:
            pass

    # ── Liquidation Heatmap (empty until live data source is connected) ──
    events.append({"type": "heatmap", "data": []})

    # ── Correlations (empty until live data source is connected) ──
    events.append({"type": "correlations", "data": []})

    return events


async def read_activity(redis: Redis) -> List[Dict[str, Any]]:
    """Build activity telemetry events for /ws/activity channel.

    Pulls from agent status keys and recent telemetry entries.
    Returns a list of typed events for useActivityChannel.
    """
    events: List[Dict[str, Any]] = []

    # Emit agent_activity events from current agent status
    async for key in redis.scan_iter(match="agent:*:status"):
        if isinstance(key, bytes):
            key = key.decode("utf-8")
        raw = await redis.get(key)
        if not raw:
            continue
        try:
            data = msgspec.json.decode(raw)
            name = key.split(":")[1]
            status_raw = data.get("status", "RED")
            status_map = {"GREEN": "complete", "READY": "complete",
                          "YELLOW": "running", "DEGRADED": "running",
                          "WARMING_UP": "running", "RED": "error",
                          "FAILED": "error"}
            events.append({
                "type": "agent_activity",
                "agentName": name,
                "category": data.get("category")
                or _AGENT_CATEGORY_FALLBACKS.get(name, "unknown"),
                "status": status_map.get(status_raw, "error"),
                "score": data.get("lastScore", data.get("last_score")),
                "maxPoints": data.get("maxPoints", data.get("max_points", 20)),
                "latencyMs": data.get("lastPingMs", data.get("last_ping_ms", 0)),
                "ragQueries": [],
                "reasoningSteps": [],
                "anomalyFlags": [],
                "cycleTs": datetime.now(UTC).isoformat(),
            })
        except Exception:
            continue

    # Pull recent telemetry entries if available
    telem_raw = await redis.lrange("polaris:telemetry:recent", 0, 19)
    for item in telem_raw:
        try:
            entry = msgspec.json.decode(item)
            events.append(entry)
        except Exception:
            continue

    return events


_GNN_PANEL_META_SLUGS = frozenset({"wallet_analysis", "lead_lag", "inference"})


def _is_per_asset_gnn_shadow_key(key_raw: str | bytes) -> bool:
    """Exclude panel aggregate keys that share the ``polaris:gnn:*:latest`` pattern."""
    ks = key_raw.decode("utf-8") if isinstance(key_raw, bytes) else key_raw
    parts = ks.split(":")
    if len(parts) != 4 or parts[0] != "polaris" or parts[1] != "gnn" or parts[3] != "latest":
        return False
    return parts[2] not in _GNN_PANEL_META_SLUGS


async def read_gnn(redis: Redis) -> Dict[str, Any]:
    keys_all = [key async for key in redis.scan_iter(match="polaris:gnn:*:latest")]
    keys = [k for k in keys_all if _is_per_asset_gnn_shadow_key(k)]
    shadow_scores = []

    if keys:
        raw_vals = await redis.mget(keys)
        for raw in raw_vals:
            if raw:
                try:
                    data = msgspec.json.decode(raw)
                    # Ignore aggregate panel keys that share the `*:latest` suffix pattern.
                    if isinstance(data, dict) and "asset" in data:
                        shadow_scores.append(data)
                except Exception:
                    pass

    wallet_analysis: Dict[str, Any] = {}
    raw_wallet = await redis.get("polaris:gnn:wallet_analysis:latest")
    if raw_wallet:
        try:
            decoded = msgspec.json.decode(raw_wallet)
            if isinstance(decoded, dict):
                wallet_analysis = decoded
        except Exception:
            pass

    lead_lag: Dict[str, Any] = {"coefficients": [], "seesaw": None}
    raw_lead = await redis.get("polaris:gnn:lead_lag:latest")
    if raw_lead:
        try:
            decoded = msgspec.json.decode(raw_lead)
            if isinstance(decoded, dict):
                lead_lag = {**lead_lag, **decoded}
        except Exception:
            pass

    inference_stats: Dict[str, Any] = {
        "latencyMs": 0,
        "nodeCount": len(keys),
        "edgeCount": 0,
        "cacheHitRate": 1.0 if keys else 0.0,
    }
    raw_inf = await redis.get("polaris:gnn:inference:latest")
    if raw_inf:
        try:
            decoded = msgspec.json.decode(raw_inf)
            if isinstance(decoded, dict):
                inference_stats = {**inference_stats, **decoded}
        except Exception:
            pass

    return {
        "shadowScores": shadow_scores,
        "walletAnalysis": wallet_analysis,
        "leadLag": lead_lag,
        "inferenceStats": inference_stats,
    }
