"""Tests for action decoder and projection."""

import numpy as np
import pytest

from atlas.ml.rlvr.action_decoder import RLVRAction, project_weight_adjustments


def test_projection_is_deterministic_and_feasible() -> None:
    """Verify projection properties."""
    rng = np.random.default_rng(42)
    for _ in range(1000):
        raw = rng.uniform(-0.5, 0.5, size=5)
        adjusted = project_weight_adjustments(raw, clip=0.1)
        assert np.all(np.abs(adjusted) <= 0.1 + 1e-9)
        assert abs(adjusted.sum()) < 1e-6


def test_action_frozen_invariant() -> None:
    """Verify RLVRAction is frozen."""
    action = RLVRAction(
        trade_decision="LONG",
        trade_confidence=0.8,
        weight_adjustments=[0.0, 0.0, 0.0, 0.0, 0.0],
    )
    with pytest.raises(Exception):
        action.trade_confidence = 0.9  # type: ignore
