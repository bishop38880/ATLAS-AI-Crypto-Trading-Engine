# SKIP_INVARIANT_CHECK
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from atlas.agents.base import AgentResult, SignalDirection
from atlas.ml.cqr_calibrator import CQRCalibrator
from atlas.ml.uncertainty_propagator import (UncertaintyBounds,
                                             UncertaintyPropagator)
from atlas.orchestrator.scorer import ConfluenceScorer
from atlas.shared.config import CQRConfig, PolarisSettings

pytestmark = pytest.mark.asyncio


async def test_propagator_disabled_fallback():
    prop = UncertaintyPropagator()
    config = CQRConfig(enabled=False)

    bounds = await prop.compute(
        conviction_point=150, cqr=config, calibration_sample_count=1000
    )

    assert bounds.method == "CQR_DISABLED"
    assert bounds.width == 0
    assert bounds.lower == 150
    assert bounds.upper == 150


async def test_propagator_insufficient_samples():
    prop = UncertaintyPropagator()
    config = CQRConfig(enabled=True, min_calibration_samples=500)

    bounds = await prop.compute(
        conviction_point=150, cqr=config, calibration_sample_count=499
    )

    assert bounds.method == "INSUFFICIENT_CALIBRATION"
    assert bounds.width == 0
    assert bounds.lower == 150


async def test_propagator_penalty_tallying():
    prop = UncertaintyPropagator()
    config = CQRConfig(enabled=True, min_calibration_samples=500)

    # tier1 (15) + timeout(5) + anomaly(8) = 28
    bounds = await prop.compute(
        conviction_point=150,
        cqr=config,
        calibration_sample_count=600,
        tier1_degraded=True,
        agent_timeout_count=1,
        anomaly_flag_count=1,
    )

    assert bounds.method == "RULE_PHASE1"
    assert bounds.width == 56  # 28 * 2
    assert bounds.lower == 150 - 28
    assert bounds.upper == 150 + 28

    assert "tier1_degraded" in bounds.degradation_sources
    assert "agent_timeout" in bounds.degradation_sources


async def test_calibrator_predict_trained():
    # Use a mock pool to avoid DB connection
    pool = AsyncMock()
    cal = CQRCalibrator(pool)

    # Mock it to appear trained
    cal._is_trained = True
    cal._model = MagicMock()
    # Predict returns (y_pred, y_pis). y_pis shape is (n_samples, 2, n_alphas)
    cal._model.predict.return_value = (
        np.array([1]),
        np.array(
            [[[100], [200]]]
        ),  # shape: (1, 2, 1) -> (n_samples, [lower, upper], n_alphas)
    )

    bounds = cal.predict_bounds(conviction_score=150, provider_health_score=0.9)
    assert bounds.method == "MAPIE_CQR"
    assert bounds.point == 150
    # width is 100 * 50 = 5000, half_width is min(35, 5000) = 35.
    # lower: 150 - 35 = 115, upper: 150 + 35 = 185
    assert bounds.lower == 115
    assert bounds.upper == 185


def _mock_scorer_deps() -> tuple[ConfluenceScorer, AsyncMock]:
    """Build a ConfluenceScorer with mocked CQR calibrator and Redis."""
    settings = PolarisSettings()
    settings.cqr = CQRConfig(enabled=True)
    cal_mock = MagicMock()
    cal_mock.is_trained.return_value = True
    cal_mock.predict_bounds.return_value = UncertaintyBounds(
        lower=120, point=150, upper=180,
        method="MOCK_CQR", width=60, degradation_sources=(),
    )
    redis_mock = MagicMock()
    redis_mock.setex = AsyncMock()
    redis_mock.get = AsyncMock()
    redis_mock.lrange = AsyncMock()
    redis_mock.incr = AsyncMock()
    redis_mock.delete = AsyncMock()
    pipe_mock = MagicMock()
    pipe_mock.execute = AsyncMock()
    redis_mock.pipeline.return_value = pipe_mock
    scorer = ConfluenceScorer(
        settings=settings, cqr_calibrator=cal_mock, redis_client=redis_mock,
    )
    return scorer, redis_mock


def _mock_agent_results() -> list[AgentResult]:
    """Build standard agent results for scorer tests."""
    return [
        AgentResult(
            agent_name="technical", score=70, max_score=100,
            explanation="test", convergences=["A"], risks=["B"],
            sub_signals={}, veto=False,
        ),
        AgentResult(
            agent_name="derivatives", score=80, max_score=100,
            explanation="test", convergences=["C"], risks=[],
            sub_signals={}, veto=False,
        ),
    ]


async def test_scorer_wiring():
    scorer, redis_mock = _mock_scorer_deps()
    agent_results = _mock_agent_results()
    sig = await scorer.score(agent_results=agent_results, asset="BTCUSDT")
    assert sig.conviction_lower == 120
    assert sig.conviction_upper == 180
    assert sig.bounds_method == "MOCK_CQR"
    assert sig.bounds_width == 60
    redis_mock.setex.assert_called_once()
    assert b"lower" in redis_mock.setex.call_args[0][2]
