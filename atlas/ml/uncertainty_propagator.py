# SKIP_INVARIANT_CHECK
"""Rule-based uncertainty bounds for raw confluence conviction (0–220 scale).

Phase 1 applies additive symmetric intervals around the point estimate without
mutating the scorer output. CQRConfig gates rule application vs fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas.shared.config import CQRConfig

_TIER1_DEGRADED_PENALTY = 15
_TIER2_DEGRADED_PENALTY = 7
_AGENT_TIMEOUT_PENALTY = 5
_ANOMALY_FLAG_PENALTY = 8
_CONSISTENCY_WARN_PENALTY = 5
_STALENESS_PENALTY = 3
_MARL_REVISION_PENALTY = 4
_PENALTY_CAP = 35
_RAW_MAX = 220


@dataclass(frozen=True, slots=True)
class UncertaintyBounds:
    """Symmetric conviction interval on the raw 220-point scale."""

    lower: int
    point: int
    upper: int
    method: str
    width: int
    degradation_sources: tuple[str, ...] = ()


def _clamp_point(point: int) -> int:
    return max(0, min(_RAW_MAX, point))


def _uncapped_penalty(
    tier1_degraded: bool,
    tier2_degraded: bool,
    agent_timeout_count: int,
    anomaly_flag_count: int,
    consistency_warn_count: int,
    staleness_count: int,
    marl_revision_count: int,
) -> int:
    at = max(0, agent_timeout_count)
    af = max(0, anomaly_flag_count)
    cw = max(0, consistency_warn_count)
    st = max(0, staleness_count)
    mr = max(0, marl_revision_count)

    total = (
        _AGENT_TIMEOUT_PENALTY * at
        + _ANOMALY_FLAG_PENALTY * af
        + _CONSISTENCY_WARN_PENALTY * cw
        + _STALENESS_PENALTY * st
        + _MARL_REVISION_PENALTY * mr
    )

    if tier1_degraded:
        total += _TIER1_DEGRADED_PENALTY
    if tier2_degraded:
        total += _TIER2_DEGRADED_PENALTY
    return total


def _degradation_sources(
    tier1_degraded: bool,
    tier2_degraded: bool,
    agent_timeout_count: int,
    anomaly_flag_count: int,
    consistency_warn_count: int,
    staleness_count: int,
    marl_revision_count: int,
) -> tuple[str, ...]:
    parts: list[str] = []
    if tier1_degraded:
        parts.append("tier1_degraded")
    if tier2_degraded:
        parts.append("tier2_degraded")
    if agent_timeout_count > 0:
        parts.append("agent_timeout")
    if anomaly_flag_count > 0:
        parts.append("anomaly_flag")
    if consistency_warn_count > 0:
        parts.append("consistency_warn")
    if staleness_count > 0:
        parts.append("staleness")
    if marl_revision_count > 0:
        parts.append("marl_revision")
    return tuple(parts)


def _fallback_bounds(
    conviction_point: int,
    method: str,
) -> UncertaintyBounds:
    p = _clamp_point(conviction_point)
    return UncertaintyBounds(
        lower=p,
        point=p,
        upper=p,
        method=method,
        width=0,
        degradation_sources=(),
    )


def _rule_bounds(
    conviction_point: int,
    penalty: int,
    sources: tuple[str, ...],
) -> UncertaintyBounds:
    p = _clamp_point(conviction_point)
    lo = max(0, p - penalty)
    hi = min(_RAW_MAX, p + penalty)
    return UncertaintyBounds(
        lower=lo,
        point=p,
        upper=hi,
        method="RULE_PHASE1",
        width=hi - lo,
        degradation_sources=sources,
    )


class UncertaintyPropagator:
    """Computes ``UncertaintyBounds`` from pipeline degradation flags."""

    async def compute(
        self,
        *,
        conviction_point: int,
        cqr: CQRConfig,
        calibration_sample_count: int,
        tier1_degraded: bool = False,
        tier2_degraded: bool = False,
        agent_timeout_count: int = 0,
        anomaly_flag_count: int = 0,
        consistency_warn_count: int = 0,
        staleness_count: int = 0,
        marl_revision_count: int = 0,
    ) -> UncertaintyBounds:
        if not cqr.enabled:
            return _fallback_bounds(conviction_point, "CQR_DISABLED")
        if calibration_sample_count < cqr.min_calibration_samples:
            return _fallback_bounds(
                conviction_point,
                "INSUFFICIENT_CALIBRATION",
            )
        return _compute_rule_penalty(
            conviction_point,
            tier1_degraded, tier2_degraded,
            agent_timeout_count, anomaly_flag_count,
            consistency_warn_count, staleness_count,
            marl_revision_count,
        )


def _compute_rule_penalty(
    conviction_point: int,
    tier1_degraded: bool,
    tier2_degraded: bool,
    agent_timeout_count: int,
    anomaly_flag_count: int,
    consistency_warn_count: int,
    staleness_count: int,
    marl_revision_count: int,
) -> UncertaintyBounds:
    """Compute capped penalty and build rule-based bounds."""
    raw = _uncapped_penalty(
        tier1_degraded, tier2_degraded,
        agent_timeout_count, anomaly_flag_count,
        consistency_warn_count, staleness_count,
        marl_revision_count,
    )
    penalty = min(_PENALTY_CAP, raw)
    sources = _degradation_sources(
        tier1_degraded, tier2_degraded,
        agent_timeout_count, anomaly_flag_count,
        consistency_warn_count, staleness_count,
        marl_revision_count,
    )
    return _rule_bounds(conviction_point, penalty, sources)
