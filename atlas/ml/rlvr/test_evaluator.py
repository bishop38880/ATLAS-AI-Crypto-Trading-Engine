"""Tests for RLVR Evaluator."""

import pytest

from atlas.ml.rlvr.evaluator import RLVREvaluator


@pytest.mark.asyncio
async def test_evaluation_significant() -> None:
    """Verify permutation test identifies significant improvement."""
    evaluator = RLVREvaluator()
    
    # Base: mostly losses
    base_pnls = [-0.01] * 80 + [0.01] * 20
    # RLVR: mostly wins
    rlvr_pnls = [0.01] * 80 + [-0.01] * 20
    
    report = await evaluator.evaluate(base_pnls, rlvr_pnls)
    
    assert report.rlvr_win_rate > report.base_win_rate
    assert report.significant is True


@pytest.mark.asyncio
async def test_evaluation_not_significant() -> None:
    """Verify permutation test rejects noise."""
    evaluator = RLVREvaluator()
    
    # Identical performance
    base_pnls = [-0.01] * 50 + [0.01] * 50
    rlvr_pnls = [-0.01] * 50 + [0.01] * 50
    
    report = await evaluator.evaluate(base_pnls, rlvr_pnls)
    
    assert report.significant is False


def test_evaluation_frozen_invariant() -> None:
    """Verify EvaluationReport is frozen."""
    evaluator = RLVREvaluator()
    # Dummy synchronous check of the schema
    from atlas.ml.rlvr.evaluator import EvaluationReport
    report = EvaluationReport(
        base_win_rate=0.5, rlvr_win_rate=0.5,
        base_avg_pnl=0.0, rlvr_avg_pnl=0.0,
        base_sharpe=0.0, rlvr_sharpe=0.0,
        p_value_win_rate=1.0, p_value_pnl=1.0,
        significant=False,
    )
    with pytest.raises(Exception):
        report.base_win_rate = 0.9  # type: ignore
