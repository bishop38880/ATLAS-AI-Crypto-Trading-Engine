"""ConfluenceScorer — aggregates agent results into a SignalOutput.

Receives a list of ``AgentResult`` instances from the pipeline
orchestrator, normalises the raw 220-point score to 0–100, determines
direction and decision, builds the ``ActionBlock`` for actionable
decisions, and returns a complete ``SignalOutput`` ready for Redis
publication.

Architecture note:
    The scorer is deterministic and sacred. Overlays (e.g. BTC Systemic
    Cascade Overlay) adjust position sizing, never the raw score. Any
    proposed change to point weights requires explicit human approval.

Session 00 hard wall:
    ``ActionBlock`` does NOT contain ``amount``. Position sizing is
    PROMETHEUS's exclusive responsibility.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4
import msgspec

if TYPE_CHECKING:
    import numpy as np

from loguru import logger

from atlas.agents.base import AgentResult, SignalDirection
from atlas.models.signal import (
    ActionBlock,
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent
from atlas.shared.config import PolarisSettings
from atlas.signals.decision_mapper import (
    calculate_decision,
    determine_direction,
)
from atlas.ml.meta_learner import StackingMetaLearner
from atlas.ml.regime_detector import RegimeResult
from atlas.ml.regime_weights import REGIME_WEIGHTS
from atlas.ml.decorrelation import calculate_decorrelated_weights
from atlas.ml.thompson_sampling import ThompsonSampler
from atlas.ml.cqr_rollout import (
    CQRRolloutStage,
    RolloutDecision,
    apply_rollout_stage,
)
from atlas.ml.uncertainty_propagator import UncertaintyBounds

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_RAW_SCORE: int = 220
_MAX_NORMALISED_SCORE: int = 100

# Agent name → category mapping
_AGENT_CATEGORY_MAP: dict[str, str] = {
    "technical": "technical",
    "derivatives": "derivatives",
    "onchain": "onchain",
    "sentiment": "sentiment",
    "whale": "whale",
    "liquidation": "liquidation",
    "regime": "regime",
    "funding_rate_monitor": "funding",
    "news_macro_agent": "news_macro",
    "correlation": "correlation",
    "risk": "risk",
    "context": "context",
    "macro": "macro",
}

# Each value is the agent's share of the 220-point raw budget (Σ = 1.0).
# Pillar mapping: derivatives 75/220 (split 20:7:6), on-chain+whale 65/220 (12:8),
# macro 30/220 (regime:news_macro:correlation:macro_cross = 5:3:2:2), sentiment 35/220,
# technical 15/220.
_DER_PILLAR = 75.0 / _MAX_RAW_SCORE
_CHAIN_PILLAR = 65.0 / _MAX_RAW_SCORE
_MACRO_PILLAR = 30.0 / _MAX_RAW_SCORE

CATEGORY_WEIGHTS: dict[str, float] = {
    "technical": 15.0 / _MAX_RAW_SCORE,
    "derivatives": _DER_PILLAR * (20.0 / 33.0),
    "liquidation": _DER_PILLAR * (7.0 / 33.0),
    "funding": _DER_PILLAR * (6.0 / 33.0),
    "onchain": _CHAIN_PILLAR * (12.0 / 20.0),
    "whale": _CHAIN_PILLAR * (8.0 / 20.0),
    "sentiment": 35.0 / _MAX_RAW_SCORE,
    "regime": _MACRO_PILLAR * (5.0 / 12.0),
    "correlation": _MACRO_PILLAR * (2.0 / 12.0),
    "macro": _MACRO_PILLAR * (2.0 / 12.0),
    "news_macro": _MACRO_PILLAR * (3.0 / 12.0),
}


class ConfluenceScorer:
    """Aggregates agent results into a complete SignalOutput.

    Attributes:
        _settings: PolarisSettings for TTL configuration.
    """

    def __init__(
        self,
        settings: PolarisSettings,
        meta_learner: StackingMetaLearner | None = None,
        thompson_sampler: ThompsonSampler | None = None,
        cqr_calibrator: Any = None,
        uncertainty_propagator: Any = None,
        redis_client: Any = None,
    ) -> None:
        """Initialize with settings for TTL lookup.

        Args:
            settings: PolarisSettings instance.
            meta_learner: Optional StackingMetaLearner instance.
            thompson_sampler: Optional ThompsonSampler instance.
        """
        self._settings = settings
        self.meta_learner = meta_learner
        self.thompson_sampler = thompson_sampler
        self._cqr_config = settings.cqr
        self._cqr_calibrator = cqr_calibrator
        self._uncertainty_propagator = uncertainty_propagator
        self._redis = redis_client
        if self._redis:
            from atlas.core.latency_monitor import LatencyMonitor
            self._latency_monitor = LatencyMonitor(self._redis, self._settings)
        else:
            self._latency_monitor = None

    async def score(
        self,
        agent_results: list[AgentResult],
        asset: str,
        timeframe: str = "30m",
        is_cascade_triggered: bool = False,
        hydra_event_id: str | None = None,
        cycle_id: str = "",
        cycle_latency_ms: float = 0.0,
        regime_result: RegimeResult | None = None,
        correlation_matrix: np.ndarray | None = None,
        agent_names: list[str] | None = None,
    ) -> SignalOutput:
        """Aggregate agent results into a SignalOutput."""
        signal_id, now = _begin_scoring(asset)
        vetoed = any(r.veto for r in agent_results)
        scoring = _filter_scoring_agents(agent_results)
        normalised = await self._score_with_latency(
            scoring, regime_result, correlation_matrix, agent_names,
        )
        normalised = _apply_suppression(normalised, agent_results)
        direction = determine_direction(scoring)
        decision = _resolve_decision(vetoed, normalised, direction)
        raw_score = sum(r.score for r in scoring)
        rollout = await self._compute_bounds_with_rollout(
            raw_score, agent_results,
        )
        await self._cache_bounds(asset, rollout.bounds)
        if self._latency_monitor:
            try:
                await self._latency_monitor.check_and_trigger()
            except Exception as exc:
                logger.warning("latency_monitor_error | err={}", exc)
        return _assemble_signal_output(
            signal_id, now, decision, asset, timeframe,
            direction, normalised, agent_results,
            is_cascade_triggered, hydra_event_id,
            cycle_id, cycle_latency_ms, self._settings,
            rollout.bounds, rollout.use_for_leverage,
        )

    async def _score_with_latency(
        self, scoring: list[AgentResult],
        regime_result: RegimeResult | None,
        correlation_matrix: np.ndarray | None,
        agent_names: list[str] | None,
    ) -> int:
        """Compute score, wrapping with latency monitor if present."""
        if self._latency_monitor:
            async with self._latency_monitor.measure("confluence_scoring"):
                return await self._compute_score(
                    scoring, regime_result, correlation_matrix, agent_names,
                )
        return await self._compute_score(
            scoring, regime_result, correlation_matrix, agent_names,
        )

    async def _compute_bounds_with_rollout(
        self, raw_score: int, agent_results: list[AgentResult],
    ) -> RolloutDecision:
        """Compute bounds then apply rollout stage gating."""
        bounds = await self._compute_raw_bounds(
            raw_score, agent_results,
        )
        stage = CQRRolloutStage(self._cqr_config.cqr_rollout_stage)
        return apply_rollout_stage(stage, bounds, raw_score)

    async def _compute_raw_bounds(
        self, raw_score: int, agent_results: list[AgentResult],
    ) -> UncertaintyBounds:
        """Compute uncertainty bounds via CQR or fallback."""
        if self._cqr_config.enabled and self._cqr_calibrator and self._cqr_calibrator.is_trained():
            return self._cqr_calibrator.predict_bounds(raw_score, 1.0)
        if self._uncertainty_propagator:
            return await self._uncertainty_propagator.compute(
                conviction_point=raw_score, cqr=self._cqr_config,
                calibration_sample_count=0,
                agent_timeout_count=sum(1 for r in agent_results if r.veto),
            )
        return UncertaintyBounds(raw_score, raw_score, raw_score, "FALLBACK", 0)

    async def _cache_bounds(self, asset: str, bounds: Any) -> None:
        """Persist bounds to Redis if available."""
        if not self._redis:
            return
        try:
            payload = msgspec.json.encode({
                "lower": bounds.lower, "upper": bounds.upper,
                "method": bounds.method, "width": bounds.width,
            })
            await self._redis.setex("polaris:bounds:{}:latest".format(asset), 3600, payload)
        except Exception as e:
            logger.warning("Failed to cache bounds in redis: {}", e)

    async def _compute_score(
        self,
        scoring: list[AgentResult],
        regime_result: RegimeResult | None,
        correlation_matrix: np.ndarray | None = None,
        agent_names: list[str] | None = None,
    ) -> int:
        """Compute normalised score via meta-learner or weights."""
        if self.meta_learner is not None and self.meta_learner.model is not None:
            _log_decorrelation_diagnostic(correlation_matrix, agent_names)
            primary_score = await self._meta_learner_score(scoring)
        elif regime_result is not None:
            primary_score = await _regime_blended_score(
                scoring, regime_result, correlation_matrix, agent_names,
            )
        else:
            primary_score = await _fixed_weight_score(
                scoring, correlation_matrix, agent_names,
            )
            
        return await self._apply_thompson_shadow(
            primary_score, scoring, agent_names,
        )

    async def _apply_thompson_shadow(
        self,
        primary_score: int,
        scoring: list[AgentResult],
        agent_names: list[str] | None,
    ) -> int:
        """Compute Thompson shadow score and log it."""
        if self.thompson_sampler is None or not agent_names:
            return primary_score

        weights = await self.thompson_sampler.get_expected_weights(agent_names)
        shadow_total = sum(
            _scaled_universal_points(r) * weights.get(r.agent_name, 0.0)
            for r in scoring
        )
        shadow_score = _normalise_score(int(round(shadow_total)))
        
        logger.info(
            "thompson_shadow | real_score={} | thompson_score={} | thompson_weights={}",
            primary_score, shadow_score, weights,
        )
        
        if getattr(self._settings, "thompson_live", False):
            logger.info("thompson_live active: overriding primary score")
            return shadow_score
        return primary_score

    async def _meta_learner_score(
        self, scoring: list[AgentResult],
    ) -> int:
        """Score using the stacking meta-learner."""
        sorted_agents = sorted(scoring, key=lambda a: a.agent_name)
        agent_scores = [float(a.score) for a in sorted_agents]
        prob = await asyncio.to_thread(
            self.meta_learner.predict, agent_scores,  # type: ignore[union-attr]
        )
        normalised = max(0, min(100, int(round(prob * 100))))
        logger.info("scoring mode: meta_learner")
        return normalised


# ---------------------------------------------------------------------------
# Pure helper functions — extracted for 40-line cap
# ---------------------------------------------------------------------------


def _begin_scoring(asset: str) -> tuple[str, datetime]:
    """Generate signal_id and log scoring start."""
    signal_id = str(uuid4())
    now = datetime.now(timezone.utc)
    logger.info("scoring start | signal_id={} | asset={}", signal_id, asset)
    return signal_id, now


def _filter_scoring_agents(
    agent_results: list[AgentResult],
) -> list[AgentResult]:
    """Filter out risk agents from scoring pool."""
    return [
        r for r in agent_results
        if r.agent_name != "risk"
        and _AGENT_CATEGORY_MAP.get(r.agent_name) != "risk"
    ]


def _apply_suppression(
    normalised: int,
    agent_results: list[AgentResult],
) -> int:
    """Apply conviction suppression, floored at 0."""
    suppression = _extract_conviction_suppression(agent_results)
    if suppression < 0:
        original = normalised
        normalised = max(0, normalised + suppression)
        logger.info(
            "conviction_suppressed | amount={} | base={} | final={}",
            suppression, original, normalised,
        )
    return normalised


def _resolve_decision(
    vetoed: bool,
    normalised: int,
    direction: SignalDirection,
) -> SignalDecision:
    """Map veto / score / direction to a decision."""
    if vetoed:
        return SignalDecision.NO_POSITION
    return calculate_decision(normalised, direction)


def _extract_conviction_suppression(
    agent_results: list[AgentResult],
) -> int:
    """Extract conviction suppression from NewsMacroAgent."""
    for r in agent_results:
        if _AGENT_CATEGORY_MAP.get(r.agent_name) == "news_macro" or r.agent_name == "news_macro_agent":
            sub = r.sub_signals.get("conviction_suppression")
            if sub is not None:
                return int(sub.metadata.get("amount", 0))
    return 0


async def _regime_blended_score(
    scoring: list[AgentResult],
    regime_result: RegimeResult,
    correlation_matrix: np.ndarray | None = None,
    agent_names: list[str] | None = None,
) -> int:
    """Compute regime-probability-blended score with decorrelation."""
    base_weights = await _apply_decorrelation(
        dict(CATEGORY_WEIGHTS), correlation_matrix, agent_names,
    )
    regime_mult = _build_regime_weight_map(scoring, regime_result)
    total = 0.0
    for r in scoring:
        cat = _AGENT_CATEGORY_MAP.get(r.agent_name)
        if cat is None:
            continue
        combined_w = base_weights.get(cat, 0.0) * regime_mult.get(cat, 1.0)
        total += _scaled_raw_points(r, combined_w)
    normalised = _normalise_score(int(round(total)))
    logger.info("scoring mode: regime_blended")
    return normalised


def _build_regime_weight_map(
    scoring: list[AgentResult],
    regime_result: RegimeResult,
) -> dict[str, float]:
    """Build a per-category weight map from regime probabilities."""
    weights: dict[str, float] = {}
    for r in scoring:
        cat = _AGENT_CATEGORY_MAP.get(r.agent_name)
        if cat is None or cat in weights:
            continue
        blended = sum(
            prob * REGIME_WEIGHTS[regime].get(cat, 1.0)
            for regime, prob in regime_result.regime_probabilities.items()
        )
        weights[cat] = blended
    return weights


async def _fixed_weight_score(
    scoring: list[AgentResult],
    correlation_matrix: np.ndarray | None = None,
    agent_names: list[str] | None = None,
) -> int:
    """Compute fixed-weight normalised score with decorrelation."""
    weights = dict(CATEGORY_WEIGHTS)
    weights = await _apply_decorrelation(
        weights, correlation_matrix, agent_names,
    )
    total = sum(
        _scaled_raw_points(
            r,
            weights.get(_AGENT_CATEGORY_MAP.get(r.agent_name) or "__none__", 0.0),
        )
        for r in scoring
        if _AGENT_CATEGORY_MAP.get(r.agent_name) is not None
    )
    normalised = _normalise_score(int(round(total)))
    logger.info("scoring mode: fixed_weights")
    return normalised


async def _apply_decorrelation(
    weights: dict[str, float],
    correlation_matrix: np.ndarray | None,
    agent_names: list[str] | None,
) -> dict[str, float]:
    """Apply decorrelation adjustment if matrix is provided."""
    if correlation_matrix is None or agent_names is None:
        return weights
    adjusted = await asyncio.to_thread(
        calculate_decorrelated_weights,
        weights, correlation_matrix, agent_names,
    )
    logger.info("decorrelation_applied | n_agents={}", len(agent_names))
    return adjusted


def _log_decorrelation_diagnostic(
    correlation_matrix: np.ndarray | None,
    agent_names: list[str] | None,
) -> None:
    """Log decorrelation info when meta-learner is active."""
    if correlation_matrix is None or agent_names is None:
        return
    from atlas.ml.decorrelation import _find_correlated_pairs
    pairs = _find_correlated_pairs(
        correlation_matrix, agent_names, 0.7,
    )
    if pairs:
        logger.info(
            "decorrelation_diagnostic_only | correlated_pairs={}",
            len(pairs),
        )


def _assemble_signal_output(
    sig_id: str,
    now: datetime,
    decision: SignalDecision,
    asset: str,
    timeframe: str,
    direction: SignalDirection,
    normalised: int,
    agents: list[AgentResult],
    is_cascade: bool,
    hydra_id: str | None,
    cycle_id: str,
    latency: float,
    settings: PolarisSettings,
    bounds: UncertaintyBounds | None = None,
    bounds_applied_to_leverage: bool = False,
) -> SignalOutput:
    """Assemble computed fields into a validated SignalOutput."""
    category_scores_payload = _build_category_scores(agents)
    sig = SignalOutput.model_construct(
        signal_id=sig_id, timestamp=now, decision=decision,
        asset=asset, timeframe=timeframe,
        action=_build_action_block(decision, direction),
        expires_at=_compute_expiry(now, timeframe, is_cascade, settings),
        reasoning_summary=_aggregate_reasoning(agents),
        key_convergences=_collect_convergences(agents),
        key_risks=_collect_risks(agents),
        is_cascade_triggered=is_cascade, hydra_event_id=hydra_id,
        score=normalised, confidence=_compute_confidence(normalised),
        category_scores=category_scores_payload,
        agent_breakdown={agent.agent_name: agent for agent in agents},
        raw_confluence_score=max(
            0,
            min(_MAX_RAW_SCORE, int(category_scores_payload.total)),
        ),
        telemetry=_build_telemetry(cycle_id, latency, len(agents), now),
        conviction_lower=bounds.lower if bounds else None,
        conviction_upper=bounds.upper if bounds else None,
        bounds_method=bounds.method if bounds else None,
        bounds_width=bounds.width if bounds else None,
    )
    return sig


def _build_telemetry(
    cycle_id: str, latency: float, agent_count: int, now: datetime,
) -> TelemetryEvent:
    """Build TelemetryEvent for the pipeline cycle."""
    return TelemetryEvent(
        cycle_id=cycle_id,
        cycle_latency_ms=latency,
        agent_count=agent_count,
        timestamp=now,
    )






def _normalise_score(raw_score: int) -> int:
    """Normalise raw 220-point score to 0–100 scale."""
    if raw_score <= 0:
        return 0
    if raw_score >= _MAX_RAW_SCORE:
        return _MAX_NORMALISED_SCORE
    return round(raw_score * _MAX_NORMALISED_SCORE / _MAX_RAW_SCORE)


def _build_category_scores(
    agent_results: list[AgentResult],
) -> CategoryScores:
    """Map each agent into its weighted share of the 220-point ladder (matches UI / WS rollup)."""
    categories = _init_category_dict()
    for result in agent_results:
        category = _AGENT_CATEGORY_MAP.get(result.agent_name)
        if category is None:
            logger.warning(
                "unknown agent category | agent={}",
                result.agent_name,
            )
            continue
        if category == "risk":
            continue
        weight_share = CATEGORY_WEIGHTS.get(category, 0.0)
        pts = int(round(_scaled_raw_points(result, weight_share)))
        categories[category] += pts

    total = (
        categories["technical"]
        + categories["derivatives"]
        + categories["onchain"]
        + categories["sentiment"]
        + categories["whale"]
        + categories["liquidation"]
        + categories["regime"]
        + categories["funding"]
        + categories["news_macro"]
        + categories["correlation"]
        + categories["macro"]
        + categories["context"]
    )
    categories["total"] = total
    return CategoryScores.model_construct(**categories)  # pyright: ignore[reportArgumentType]


def _init_category_dict() -> dict[str, int]:
    """Create the zero-initialised category score dictionary."""
    return {
        "technical": 0, "derivatives": 0, "onchain": 0,
        "sentiment": 0, "whale": 0, "liquidation": 0,
        "regime": 0, "funding": 0, "news_macro": 0,
        "correlation": 0, "macro": 0, "context": 0,
    }


def _scaled_raw_points(agent: AgentResult, category_weight: float) -> float:
    """Contribution of *agent* to the 220-point raw total from its category weight."""
    if category_weight <= 0.0 or agent.max_score <= 0:
        return 0.0
    ratio = min(1.0, max(0.0, agent.score / agent.max_score))
    return ratio * float(_MAX_RAW_SCORE) * category_weight


def _scaled_universal_points(agent: AgentResult) -> float:
    """Map native agent score to 0–220 universal units (Thompson shadow path)."""
    if agent.max_score <= 0:
        return 0.0
    ratio = min(1.0, max(0.0, agent.score / agent.max_score))
    return ratio * float(_MAX_RAW_SCORE)


def _compute_expiry(
    now: datetime,
    timeframe: str,
    is_cascade: bool,
    settings: PolarisSettings,
) -> datetime:
    """Compute signal expiration from config TTL."""
    if is_cascade:
        ttl_minutes = settings.cascade_signal_ttl_minutes
    else:
        ttl_minutes = settings.signal_ttl_minutes.get(timeframe, 30)
    return now + timedelta(minutes=ttl_minutes)


def _build_action_block(
    decision: SignalDecision,
    direction: SignalDirection,
) -> ActionBlock | None:
    """Build ActionBlock for actionable decisions.

    No ``amount`` field — position sizing is PROMETHEUS's concern.
    """
    actionable = {
        SignalDecision.BUY, SignalDecision.STRONG_BUY,
        SignalDecision.SELL, SignalDecision.STRONG_SELL,
    }
    if decision not in actionable:
        return None
    side = "buy" if direction == SignalDirection.BULLISH else "sell"
    return ActionBlock.model_construct(
        side=side, order_type="limit",
        price=Decimal("1"), stop_loss=Decimal("1"),
        take_profit=Decimal("1"),
    )


def _compute_confidence(normalised_score: int) -> Decimal:
    """Derive confidence from normalised score."""
    return (Decimal(str(normalised_score)) / Decimal("100")).quantize(Decimal("0.01"))


def _aggregate_reasoning(
    agent_results: list[AgentResult],
) -> str:
    """Build reasoning summary from agent explanations."""
    explanations = [
        r.explanation for r in agent_results if r.explanation
    ]
    return "; ".join(explanations)


def _collect_convergences(
    agent_results: list[AgentResult],
) -> list[str]:
    """Flatten convergences from all agents."""
    seen: set[str] = set()
    result: list[str] = []
    for agent in agent_results:
        for c in agent.convergences:
            if c not in seen:
                seen.add(c)
                result.append(c)
    return result


def _collect_risks(
    agent_results: list[AgentResult],
) -> list[str]:
    """Flatten risks from all agents."""
    seen: set[str] = set()
    result: list[str] = []
    for agent in agent_results:
        for r in agent.risks:
            if r not in seen:
                seen.add(r)
                result.append(r)
    return result
