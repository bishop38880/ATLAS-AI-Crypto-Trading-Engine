"""A/B Evaluator for RLVR vs Base Scorer."""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import numpy as np
from pydantic import BaseModel
from scipy.stats import permutation_test


class EvaluationReport(BaseModel, frozen=True):
    """Immutable evaluation report."""
    base_win_rate: float
    rlvr_win_rate: float
    base_avg_pnl: float
    rlvr_avg_pnl: float
    base_sharpe: float
    rlvr_sharpe: float
    p_value_win_rate: float
    p_value_pnl: float
    significant: bool


def _empty_report() -> EvaluationReport:
    """Return a zeroed-out report for insufficient data."""
    return EvaluationReport(
        base_win_rate=0.0, rlvr_win_rate=0.0,
        base_avg_pnl=0.0, rlvr_avg_pnl=0.0,
        base_sharpe=0.0, rlvr_sharpe=0.0,
        p_value_win_rate=1.0, p_value_pnl=1.0,
        significant=False,
    )


def _compute_basic_stats(base: np.ndarray, rlvr: np.ndarray) -> dict:
    """Compute win rates, mean PnLs, and Sharpe ratios."""
    base_mean = float(np.mean(base))
    rlvr_mean = float(np.mean(rlvr))
    return {
        "base_win_rate": float(np.mean((base > 0).astype(float))),
        "rlvr_win_rate": float(np.mean((rlvr > 0).astype(float))),
        "base_avg_pnl": base_mean,
        "rlvr_avg_pnl": rlvr_mean,
        "base_sharpe": float(base_mean / (np.std(base) + 1e-9)),
        "rlvr_sharpe": float(rlvr_mean / (np.std(rlvr) + 1e-9)),
    }


class RLVREvaluator:
    """Evaluates RLVR performance vs base scorer using permutation tests."""

    def __init__(self, p_value_threshold: float = 0.05) -> None:
        self.p_value_threshold = p_value_threshold

    async def evaluate(
        self,
        base_pnls: list[float],
        rlvr_pnls: list[float],
    ) -> EvaluationReport:
        """Run A/B evaluation with permutation testing."""
        if not base_pnls or not rlvr_pnls:
            return _empty_report()

        base_arr = np.array(base_pnls)
        rlvr_arr = np.array(rlvr_pnls)
        stats = _compute_basic_stats(base_arr, rlvr_arr)

        def _mean_diff(x: np.ndarray, y: np.ndarray) -> float:
            return float(np.mean(x) - np.mean(y))

        base_wins = (base_arr > 0).astype(float)
        rlvr_wins = (rlvr_arr > 0).astype(float)

        p_val_wr = await asyncio.to_thread(
            self._run_permutation_test, rlvr_wins, base_wins, _mean_diff,
        )
        p_val_pnl = await asyncio.to_thread(
            self._run_permutation_test, rlvr_arr, base_arr, _mean_diff,
        )

        return EvaluationReport(
            **stats,
            p_value_win_rate=p_val_wr,
            p_value_pnl=p_val_pnl,
            significant=bool(p_val_pnl < self.p_value_threshold),
        )

    def _run_permutation_test(self, x: np.ndarray, y: np.ndarray, statistic: Callable[..., Any]) -> float:
        """Run permutation test synchronously."""
        res = permutation_test(
            (x, y), statistic, permutation_type='independent', alternative='greater', n_resamples=1000
        )
        return float(res.pvalue)
