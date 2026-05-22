"""PCA Diagnostic — agent diversity analysis (logging only).

Runs PCA on the rolling window of agent scores and reports how many
principal components are needed to explain 95 % of variance.  If fewer
than 4 components suffice, the agent pool may be redundant.

**This module does NOT modify the scoring pipeline.**  It is for
logging and alerting only.
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import numpy as np
from loguru import logger
from pydantic import BaseModel, Field
from sklearn.decomposition import PCA


class PCADiagnosticResult(BaseModel, frozen=True):
    """Immutable PCA diagnostic output."""

    n_components_95pct: int = Field(
        description="Components explaining ≥ 95 % variance"
    )
    explained_variance_ratios: list[float] = Field(
        description="Per-component variance ratios (native float)"
    )
    is_diversity_low: bool = Field(
        description="True when n_components_95pct < 4"
    )


# ------------------------------------------------------------------
# Core diagnostic (synchronous — caller wraps in to_thread)
# ------------------------------------------------------------------


def run_pca_diagnostic(
    score_history: np.ndarray,
    agent_names: list[str],
) -> PCADiagnosticResult:
    """Run PCA on *score_history* and report component count.

    Args:
        score_history: ``(n_cycles, n_agents)`` array.
        agent_names: Agent labels (used for logging only).

    Returns:
        ``PCADiagnosticResult`` with native Python types.
    """
    n_components = min(score_history.shape[0], score_history.shape[1])
    pca = PCA(n_components=n_components)
    pca.fit(score_history)

    ratios = [float(r) for r in pca.explained_variance_ratio_]
    n_95 = _count_components_for_threshold(ratios, 0.95)
    low = n_95 < 4

    if low:
        logger.warning(
            "agent_diversity_critically_low | n_components={} | total_agents={}",
            n_95,
            len(agent_names),
        )
    else:
        logger.info(
            "pca_diagnostic | n_components_95pct={} | agents={}",
            n_95,
            len(agent_names),
        )

    return PCADiagnosticResult.model_construct(
        n_components_95pct=n_95,
        explained_variance_ratios=ratios,
        is_diversity_low=low,
    )


def _count_components_for_threshold(
    ratios: list[float], threshold: float
) -> int:
    """Return the minimum number of components reaching *threshold*."""
    cumulative = 0.0
    for idx, ratio in enumerate(ratios, start=1):
        cumulative += ratio
        if cumulative >= threshold:
            return idx
    return len(ratios)


# ------------------------------------------------------------------
# PostgreSQL persistence
# ------------------------------------------------------------------


async def store_pca_result(
    pool: asyncpg.Pool,  # type: ignore[type-arg]
    result: PCADiagnosticResult,
) -> None:
    """Persist a PCA diagnostic result for trend tracking.

    Args:
        pool: An ``asyncpg`` connection pool.
        result: The diagnostic result to store.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pca_diagnostics
                (timestamp, n_components, variance_ratios, diversity_low)
            VALUES ($1, $2, $3, $4)
            """,
            datetime.now(timezone.utc),
            result.n_components_95pct,
            result.explained_variance_ratios,
            result.is_diversity_low,
            timeout=10.0,
        )
    logger.info("pca_result_stored | n_components={}", result.n_components_95pct)
