"""Tests for correlation-aware weight adjustment."""

from __future__ import annotations

import numpy as np
import pytest

from atlas.ml.decorrelation import calculate_decorrelated_weights


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_AGENTS = ["agent_a", "agent_b", "agent_c"]


def _identity_corr() -> np.ndarray:
    """Return a 3×3 identity correlation matrix (no correlation)."""
    return np.eye(3)


def _high_corr_ab() -> np.ndarray:
    """Return a 3×3 matrix where agent_a↔agent_b have ρ = 0.9."""
    m = np.eye(3)
    m[0, 1] = 0.9
    m[1, 0] = 0.9
    return m


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestDecorrelation:
    """Verify weight adjustment for correlated and uncorrelated agents."""

    def test_uncorrelated_agents_keep_original_weights(self) -> None:
        """No correlation → weights unchanged (after normalisation)."""
        raw = {"agent_a": 0.4, "agent_b": 0.4, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _identity_corr(), _AGENTS
        )
        assert abs(result["agent_a"] - 0.4) < 1e-6
        assert abs(result["agent_b"] - 0.4) < 1e-6
        assert abs(result["agent_c"] - 0.2) < 1e-6

    def test_correlated_pair_has_reduced_combined_weight(self) -> None:
        """ρ(a,b) = 0.9 → combined weight drops."""
        raw = {"agent_a": 0.4, "agent_b": 0.4, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _high_corr_ab(), _AGENTS
        )
        combined = result["agent_a"] + result["agent_b"]
        # Original combined = 0.8.
        # After decorrelation the effective combined should be lower
        # relative to agent_c whose weight rises after normalisation.
        assert result["agent_c"] > 0.2

    def test_all_weights_positive(self) -> None:
        """No weight should go negative after adjustment."""
        raw = {"agent_a": 0.4, "agent_b": 0.4, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _high_corr_ab(), _AGENTS
        )
        for v in result.values():
            assert v > 0

    def test_weights_sum_to_one(self) -> None:
        """Adjusted weights must sum to 1.0."""
        raw = {"agent_a": 0.4, "agent_b": 0.4, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _high_corr_ab(), _AGENTS
        )
        assert abs(sum(result.values()) - 1.0) < 1e-9

    def test_output_values_are_native_float(self) -> None:
        """All returned weights must be ``float``, not ``np.float64``."""
        raw = {"agent_a": 0.5, "agent_b": 0.3, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _high_corr_ab(), _AGENTS
        )
        for v in result.values():
            assert type(v) is float

    def test_symmetry_preserved(self) -> None:
        """Equal-weight correlated agents get equal adjusted weights."""
        raw = {"agent_a": 0.4, "agent_b": 0.4, "agent_c": 0.2}
        result = calculate_decorrelated_weights(
            raw, _high_corr_ab(), _AGENTS
        )
        assert abs(result["agent_a"] - result["agent_b"]) < 1e-9
