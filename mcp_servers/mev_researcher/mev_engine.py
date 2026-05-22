"""Heuristic mempool toxicity and routing primitives."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from loguru import logger

from .models import ExecutionRecommendation, PendingMempoolEvent, RugScanResult, ToxicityReport

ZERO: Decimal = Decimal("0")
ChainLiteral = Literal["ethereum", "solana"]
BiasLiteral = PendingMempoolEvent.__annotations__["direction_bias"]
ExecStrategyLiteral = ExecutionRecommendation.__annotations__["recommended_strategy"]


def calculate_mempool_toxicity_metrics(
    events: list[PendingMempoolEvent],
) -> tuple[Decimal, list[str]]:
    """Derive normalized MTI and descriptive threat buckets."""
    if not events:
        return Decimal("0.15"), ["no_recent_pending_flow"]

    buy_volume: Decimal = ZERO
    sell_volume: Decimal = ZERO
    total_volume: Decimal = ZERO

    weighted_priority_sum: Decimal = ZERO
    complexity_sum: Decimal = ZERO

    searcher_like: bool = False

    for event in events:
        total_volume += event.notional_usd

        if event.direction_bias == "buy":
            buy_volume += event.notional_usd

        elif event.direction_bias == "sell":
            sell_volume += event.notional_usd

        weighted_priority_sum += event.notional_usd * (
            Decimal.max(ZERO, event.priority_fee_micros)
        )

        complexity_sum += Decimal.max(ZERO, event.complexity_score)

        if event.priority_fee_micros > Decimal("750"):
            searcher_like = True

    directional_skew_ratio: Decimal = ZERO

    if total_volume > ZERO:
        directional_skew_ratio = (buy_volume - sell_volume).copy_abs()
        directional_skew_ratio = directional_skew_ratio / total_volume

    directional_component: Decimal = Decimal.min(
        Decimal("1"),
        directional_skew_ratio.copy_abs(),
    )

    normalized_priority_fee: Decimal = ZERO

    if total_volume > ZERO:
        weighted_mean_priority: Decimal = weighted_priority_sum / total_volume
        scaled: Decimal = weighted_mean_priority / Decimal("1500")
        normalized_priority_fee = Decimal.min(Decimal("1"), scaled)

    complexity_average: Decimal = ZERO

    if events:
        complexity_average = complexity_sum / Decimal(len(events))
        complexity_average = Decimal.min(Decimal("1"), complexity_average)

    searcher_component: Decimal = Decimal("0.85") if searcher_like else ZERO

    mti_estimate: Decimal = (
        directional_component * Decimal("0.40")
        + normalized_priority_fee * Decimal("0.35")
        + complexity_average * Decimal("0.10")
        + searcher_component * Decimal("0.15")
    )
    clamped_mti: Decimal = Decimal.min(Decimal("1"), Decimal.max(ZERO, mti_estimate))

    tags: list[str] = []

    if directional_skew_ratio > Decimal("0.55"):
        tags.append("directional_concentration")

    if normalized_priority_fee > Decimal("0.45"):
        tags.append("searcher_pressure")

    if searcher_like:
        tags.append("searcher_spike")

    if complexity_average > Decimal("0.60"):
        tags.append("routing_complexity")

    if directional_skew_ratio > Decimal("0.35") and complexity_average > Decimal(
        "0.45",
    ):
        tags.append("sandwich_pressure")

    if not tags:
        tags.append("benign_uncertainty")

    return clamped_mti, tags


def calculate_toxicity_report(
    *,
    chain: ChainLiteral,
    pool_address: str,
    events: list[PendingMempoolEvent],
    degraded_flag: bool,
    degraded_reason: str,
    net_volume_override: Decimal | None = None,
) -> ToxicityReport:
    """Wrap MTI primitives into API model."""
    mti, tags = calculate_mempool_toxicity_metrics(events)

    buy_volume_local: Decimal = ZERO
    sell_volume_local: Decimal = ZERO

    for event in events:
        if event.direction_bias == "buy":
            buy_volume_local += event.notional_usd

        elif event.direction_bias == "sell":
            sell_volume_local += event.notional_usd

    directional_net_volume: Decimal = (buy_volume_local - sell_volume_local).copy_abs()
    display_volume: Decimal = directional_net_volume

    if net_volume_override is not None:
        display_volume = net_volume_override

    if degraded_flag:
        degraded_tags: list[str] = tags + ["degraded_feed"]
        return ToxicityReport(
            chain=chain,
            pool_address=pool_address,
            mempool_toxicity_index=mti,
            pending_volume_usd=display_volume,
            threat_types=degraded_tags,
            degraded=True,
            degraded_reason=degraded_reason,
        )

    return ToxicityReport(
        chain=chain,
        pool_address=pool_address,
        mempool_toxicity_index=mti,
        pending_volume_usd=display_volume,
        threat_types=tags,
        degraded=False,
        degraded_reason="",
    )


def simulate_sandwich_hazard(
    *,
    intended_direction: str,
    events: list[PendingMempoolEvent],
    mti: Decimal,
    size_usd: Decimal,
) -> bool:
    """Heuristic large-order vulnerability flag."""
    if mti >= Decimal("0.75"):
        return True

    if intended_direction.upper() == "LONG":
        contrary_bias: BiasLiteral = "sell"

    else:
        contrary_bias = "buy"

    hostile_volume: Decimal = ZERO

    for event in events:
        if event.direction_bias == contrary_bias:
            hostile_volume += event.notional_usd

    if hostile_volume > size_usd * Decimal("3") and mti >= Decimal("0.40"):
        return True

    if hostile_volume > size_usd * Decimal("1.50") and mti >= Decimal("0.62"):
        return True

    return False


def calculate_execution_strategy(
    *,
    mti: Decimal,
    hazards: bool,
) -> ExecStrategyLiteral:
    """Deterministic PROMETHEUS strategy mapping."""
    hazard_abort: bool = hazards and mti >= Decimal("0.70")

    if mti >= Decimal("0.92") or hazard_abort:
        return "ABORT"

    if hazards and mti >= Decimal("0.55"):
        return "PROTECTIVE_SWARM"

    if mti >= Decimal("0.70"):
        return "PROTECTIVE_SWARM"

    if mti >= Decimal("0.30"):
        return "ICEBERG"

    return "VWAP"


def build_execution_recommendation(
    *,
    intended_direction: str,
    chain: ChainLiteral,
    pool_address: str,
    events: list[PendingMempoolEvent],
    size_usd: Decimal,
) -> ExecutionRecommendation:
    """Combine MTI simulation with PROMETHEUS routing advice."""

    del chain, pool_address  # Explicitly acknowledged for MCP tracing hooks

    mti, descriptive_tags = calculate_mempool_toxicity_metrics(events)
    hazardous: bool = simulate_sandwich_hazard(
        intended_direction=intended_direction,
        events=events,
        mti=mti,
        size_usd=size_usd,
    )

    strategy: ExecStrategyLiteral = calculate_execution_strategy(
        mti=mti,
        hazards=hazardous,
    )

    snippet: str = ", ".join(descriptive_tags[:5])
    rationale: str = (
        "Section 25.5 Architecture: Protective Swarms and "
        "MEV-Aware Execution. MTI {:.4f}; tags [{}]; hazard={}".format(
            float(mti),
            snippet,
            hazardous,
        )
    )

    return ExecutionRecommendation(
        recommended_strategy=strategy,
        rationale=rationale,
        mti=mti,
        simulated_sandwich_hazard=hazardous,
    )


def evaluate_liquidity_drain_signals(
    *,
    chain: ChainLiteral,
    pool_address: str,
    events: list[PendingMempoolEvent],
) -> RugScanResult:
    """Interpret cached removal heuristics."""

    flagged: bool = any(event.liquidity_removal_candidate for event in events)

    return RugScanResult(
        drain_detected=flagged,
        chain=chain,
        pool_address=pool_address,
    )


def coerce_decimal(value: Decimal | float | str) -> Decimal:
    """Parse external numeric fields safely."""
    try:
        if isinstance(value, Decimal):
            return value

        return Decimal(str(value))

    except ArithmeticError as exc:
        logger.warning("decimal parse failed | value={} | err={}", value, exc)
        return ZERO
