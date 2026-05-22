"""Correlation-Aware Weight Adjustment.

For pairs of agents whose |ρ| > 0.7 the combined weight is reduced so
that highly correlated signals do not double-count.  The adjustment is
proportional: each agent in the pair retains weight according to its
original share.

This module is pure math (no I/O).  Callers on an async path should
wrap in ``asyncio.to_thread()`` when dealing with large matrices.
"""

from __future__ import annotations

import numpy as np
from loguru import logger

_DEFAULT_THRESHOLD: float = 0.7


def calculate_decorrelated_weights(
    raw_weights: dict[str, float],
    correlation_matrix: np.ndarray,
    agent_names: list[str],
    threshold: float = _DEFAULT_THRESHOLD,
) -> dict[str, float]:
    """Adjust *raw_weights* to penalise correlated agent pairs.

    Args:
        raw_weights: Agent name → weight mapping.
        correlation_matrix: ``(n, n)`` correlation matrix.
        agent_names: Ordered list matching matrix indices.
        threshold: |ρ| above which pairs are penalised.

    Returns:
        Adjusted weight dict summing to 1.0, native ``float`` values.
    """
    adjusted = dict(raw_weights)
    pairs = _find_correlated_pairs(correlation_matrix, agent_names, threshold)

    for name_a, name_b, rho in pairs:
        _adjust_pair(adjusted, name_a, name_b, rho)
        logger.info(
            "decorrelation_adjustment | {}↔{} | ρ={:.3f}",
            name_a, name_b, rho,
        )

    return _normalize_weights(adjusted)


# ------------------------------------------------------------------
# Helpers (≤ 40 lines each)
# ------------------------------------------------------------------


def _find_correlated_pairs(
    corr: np.ndarray,
    names: list[str],
    threshold: float,
) -> list[tuple[str, str, float]]:
    """Return pairs from the upper triangle exceeding *threshold*."""
    pairs: list[tuple[str, str, float]] = []
    n = len(names)
    for i in range(n):
        for j in range(i + 1, n):
            rho = float(corr[i, j])
            if abs(rho) > threshold:
                pairs.append((names[i], names[j], rho))
    return pairs


def _adjust_pair(
    weights: dict[str, float],
    name_a: str,
    name_b: str,
    rho: float,
) -> None:
    """Reduce combined weight for a single correlated pair *in-place*.

    Effective combined weight = ``w_a + w_b * (1 − |ρ|)``.
    The effective weight is then split proportionally to original
    individual weights.
    """
    w_a = weights.get(name_a, 0.0)
    w_b = weights.get(name_b, 0.0)
    total_original = w_a + w_b
    if total_original <= 0:
        return

    effective = w_a + w_b * (1.0 - abs(rho))
    ratio_a = w_a / total_original
    ratio_b = w_b / total_original

    weights[name_a] = max(effective * ratio_a, 1e-9)
    weights[name_b] = max(effective * ratio_b, 1e-9)


def _normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Re-normalise weights to sum to 1.0, all native ``float``."""
    total = sum(weights.values())
    if total <= 0:
        n = len(weights) or 1
        return {k: float(1.0 / n) for k in weights}
    return {k: float(v / total) for k, v in weights.items()}
