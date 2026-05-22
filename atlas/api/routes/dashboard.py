from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Iterable, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis

from atlas.api._channel_reads import read_prices, read_scores_ws_broadcast
from atlas.api.asset_correlation_matrix import (
    CACHE_TTL_SECONDS,
    analyse_position_concentration,
    load_or_compute_correlation_snapshot,
)
from atlas.api.dashboard_rotation_pairs import (
    MAX_DASHBOARD_MONITORED_ASSETS,
    calculate_dashboard_pairs,
)
from atlas.api.schemas import _BaseConfig
from atlas.core.autonomous_rag_analysis import (
    DASHBOARD_PINS_REDIS_KEY,
    normalise_analysis_symbol,
)
from atlas.api.dashboard_regime_payload import (
    RegimeControlCenterPayload,
    build_regime_control_center_payload,
)
from atlas.dependencies import get_redis
from atlas.shared.config import PolarisSettings


router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

async def persist_operator_dashboard_pins(redis: Redis, pairs: list[str]) -> int:
    """Replace ``polaris:dashboard:pins`` with normalized ``BASE/USDT`` symbols."""
    normalized: list[str] = []
    seen_symbols: set[str] = set()
    for raw_pair in pairs:
        symbol = normalise_analysis_symbol(raw_pair.strip())
        if symbol is None:
            continue
        upper_symbol = symbol.upper()
        if upper_symbol in seen_symbols:
            continue
        seen_symbols.add(upper_symbol)
        normalized.append(symbol)
        if len(normalized) >= MAX_DASHBOARD_MONITORED_ASSETS:
            break

    pipe = redis.pipeline()
    pipe.delete(DASHBOARD_PINS_REDIS_KEY)
    if normalized:
        pipe.sadd(DASHBOARD_PINS_REDIS_KEY, *normalized)
    await pipe.execute()
    return len(normalized)


class DashboardPinsBody(BaseModel):
    model_config = _BaseConfig

    pairs: list[str] = Field(default_factory=list, max_length=MAX_DASHBOARD_MONITORED_ASSETS)


class DashboardAssetSnapshot(BaseModel):
    model_config = _BaseConfig

    asset: str
    price: str
    funding_rate: str
    open_interest: str
    total_score: int
    normalized_score: int
    decision: str
    confidence: float
    gate_threshold: int
    category_scores: dict[str, int]
    llm_tier_label: str
    cycle_number: int | None
    cycle_timestamp: str
    obti_summary: str | None
    obti_side: str | None


class DashboardSnapshot(BaseModel):
    model_config = _BaseConfig

    generated_at: str
    assets: list[DashboardAssetSnapshot]


class CorrelationClusterPayload(BaseModel):
    model_config = _BaseConfig

    bases: list[str] = Field(description="Assets in this highly correlated component.")
    size: int = Field(description="Component cardinality.")
    max_abs_correlation: float = Field(
        description="Maximum absolute pairwise correlation inside the component.",
    )


class ExtremeCorrelationPairPayload(BaseModel):
    model_config = _BaseConfig

    base_a: str = Field(description="First leg base asset.")
    base_b: str = Field(description="Second leg base asset.")
    correlation: float = Field(description="Pearson ρ on the 30-day daily-return window.")


class PositionCorrelationRiskPayload(BaseModel):
    model_config = _BaseConfig

    level: Literal["ok", "warn", "critical"] = Field(
        description="Severity versus diversification targets.",
    )
    message: str = Field(description="Operator-facing explanation.")
    average_pairwise_correlation: float | None = Field(
        default=None,
        description="Mean ρ across monitored open positions (same window as parent matrix).",
    )
    monitored_positions: list[str] = Field(
        default_factory=list,
        description="BASE symbols that matched this ladder universe.",
    )


class AssetCorrelationMatrixPayload(BaseModel):
    model_config = _BaseConfig

    generated_at: str = Field(description="UTC ISO8601 timestamp for this snapshot.")
    pairs: list[str] = Field(description="Canonical ``BASE/USDT`` ladder contracts.")
    bases: list[str] = Field(description="Parallel uppercase BASE symbols.")
    matrix_14d: list[list[float | None]] = Field(
        description="Symmetric Pearson matrix on the last 14 daily log-return observations.",
    )
    matrix_30d: list[list[float | None]] = Field(
        description="Symmetric Pearson matrix on the last 30 daily log-return observations.",
    )
    clusters_30d: list[CorrelationClusterPayload] = Field(
        description="Connected components where |ρ| ≥ 0.85 on the 30-day window.",
    )
    extreme_pairs_30d: list[ExtremeCorrelationPairPayload] = Field(
        description="Pairs with ρ ≥ 0.92 on the 30-day window.",
    )
    fetch_errors: dict[str, str] = Field(
        default_factory=dict,
        description="Assets missing Binance USDM history.",
    )
    cache_ttl_seconds: int = Field(description="Redis cache TTL for recomputation.")
    position_risk_14d: PositionCorrelationRiskPayload
    position_risk_30d: PositionCorrelationRiskPayload


def _decode_rotation_members(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, (set, frozenset)):
        iterable = raw
    elif isinstance(raw, (list, tuple)):
        iterable = raw
    else:
        iterable = cast(Iterable[Any], raw)
    decoded = [
        item.decode("utf-8") if isinstance(item, bytes) else str(item)
        for item in iterable
    ]
    return sorted(decoded)


async def _read_dashboard_asset_pairs(redis: Redis) -> list[str]:
    raw_members = await redis.smembers("polaris:rotation:active_33")  # type: ignore[union-attr,misc]  # type: ignore[union-attr]
    entries = _decode_rotation_members(raw_members)
    return calculate_dashboard_pairs(entries)


@router.get("/snapshot", response_model=DashboardSnapshot, response_model_by_alias=True)
async def get_dashboard_snapshot(request: Request) -> DashboardSnapshot:
    """Return one REST bootstrap payload matching the WebSocket score contract."""
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()
    scores = await read_scores_ws_broadcast(redis, settings)
    prices = await read_prices(redis, settings)
    price_by_symbol = {price.symbol.upper(): price for price in prices}

    assets: list[DashboardAssetSnapshot] = []
    for score in scores:
        symbol = score.asset.upper()
        price = price_by_symbol.get(symbol)
        assets.append(
            DashboardAssetSnapshot(
                asset=score.asset,
                price=price.price if price is not None else score.price,
                funding_rate=score.funding_rate,
                open_interest=score.open_interest,
                total_score=score.total_score,
                normalized_score=score.normalized_score,
                decision=score.decision,
                confidence=score.confidence,
                gate_threshold=score.gate_threshold,
                category_scores=score.category_scores,
                llm_tier_label=score.llm_tier_label,
                cycle_number=score.cycle_number,
                cycle_timestamp=score.cycle_timestamp or score.timestamp,
                obti_summary=score.obti_summary,
                obti_side=score.obti_side,
            )
        )

    return DashboardSnapshot(
        generated_at=datetime.now(timezone.utc).isoformat(),
        assets=assets,
    )


@router.get(
    "/regime-control-center",
    response_model=RegimeControlCenterPayload,
    response_model_by_alias=True,
)
async def get_regime_control_center(request: Request) -> RegimeControlCenterPayload:
    """Aggregate BTC regime state, classification drivers, adjustments, and 7d timeline."""
    redis: Redis = request.app.state.redis
    settings = PolarisSettings()
    return await build_regime_control_center_payload(redis, settings, focus_asset="BTC")


@router.get(
    "/correlation-matrix",
    response_model=AssetCorrelationMatrixPayload,
    response_model_by_alias=True,
)
async def get_asset_correlation_matrix(
    request: Request,
    positions: Annotated[
        str | None,
        Query(
            description=(
                "Comma-separated BASE symbols for active PROMETHEUS ladder slots "
                "(e.g. ``BTC,ETH,SOL``)."
            ),
        ),
    ] = None,
) -> AssetCorrelationMatrixPayload:
    """Rolling 14d / 30d correlations for the 33-card dashboard ladder."""
    atlas_state = getattr(request.app.state, "atlas", None)
    http_client = getattr(atlas_state, "dashboard_market_http", None) if atlas_state else None
    if http_client is None:
        raise HTTPException(status_code=503, detail="dashboard_market_http_unavailable")

    redis = request.app.state.redis
    pairs = await _read_dashboard_asset_pairs(redis)
    snapshot = await load_or_compute_correlation_snapshot(redis, http_client, pairs)

    position_tokens = (
        [token.strip() for token in positions.split(",") if token.strip()]
        if positions
        else []
    )
    risk_14 = analyse_position_concentration(snapshot.matrix_14, snapshot.bases, position_tokens)
    risk_30 = analyse_position_concentration(snapshot.matrix_30, snapshot.bases, position_tokens)

    cluster_models = [CorrelationClusterPayload(**row) for row in snapshot.clusters_30]
    extreme_models = [
        ExtremeCorrelationPairPayload(
            base_a=str(row["base_a"]),
            base_b=str(row["base_b"]),
            correlation=float(row["correlation"]),
        )
        for row in snapshot.extreme_pairs_30
    ]

    return AssetCorrelationMatrixPayload(
        generated_at=snapshot.generated_at,
        pairs=snapshot.pairs,
        bases=snapshot.bases,
        matrix_14d=snapshot.matrix_14,
        matrix_30d=snapshot.matrix_30,
        clusters_30d=cluster_models,
        extreme_pairs_30d=extreme_models,
        fetch_errors=snapshot.fetch_errors,
        cache_ttl_seconds=CACHE_TTL_SECONDS,
        position_risk_14d=PositionCorrelationRiskPayload(**risk_14),
        position_risk_30d=PositionCorrelationRiskPayload(**risk_30),
    )


@router.post("/pins")
async def sync_dashboard_pins(
    body: DashboardPinsBody,
    redis: Redis = Depends(get_redis),
) -> dict[str, int]:
    """Persist operator-pinned dashboard pairs so the autonomous scorer monitors them."""
    stored = await persist_operator_dashboard_pins(redis, list(body.pairs))
    return {"stored": stored}
