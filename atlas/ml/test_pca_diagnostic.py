"""Tests for PCA diagnostic module."""

from __future__ import annotations

import numpy as np
import pytest

from atlas.ml.pca_diagnostic import PCADiagnosticResult, run_pca_diagnostic


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

_AGENT_NAMES = [f"agent_{i}" for i in range(10)]


def _make_identical_signals(n_cycles: int = 50) -> np.ndarray:
    """All 10 agents produce the same signal each cycle."""
    base = np.arange(n_cycles, dtype=np.float64)
    return np.column_stack([base] * 10)


def _make_independent_signals(n_cycles: int = 200) -> np.ndarray:
    """10 agents with fully independent random signals."""
    rng = np.random.default_rng(123)
    return rng.random((n_cycles, 10))


# ------------------------------------------------------------------
# Tests
# ------------------------------------------------------------------


class TestPCADiagnostic:
    """Verify PCA component counting and diversity alerts."""

    def test_identical_signals_one_component(self) -> None:
        """10 identical signals → 1 component at 95 %."""
        history = _make_identical_signals()
        result = run_pca_diagnostic(history, _AGENT_NAMES)
        assert result.n_components_95pct == 1
        assert result.is_diversity_low is True

    def test_independent_signals_many_components(self) -> None:
        """10 independent signals → ~10 components at 95 %."""
        history = _make_independent_signals()
        result = run_pca_diagnostic(history, _AGENT_NAMES)
        # With truly independent signals we expect most components needed
        assert result.n_components_95pct >= 7

    def test_diversity_warning_when_low(self) -> None:
        """``is_diversity_low`` is True when components < 4."""
        # Create 10 signals that are mostly copies of 2 sources
        rng = np.random.default_rng(99)
        base_a = rng.random(100)
        base_b = rng.random(100)
        cols = []
        for i in range(10):
            noise = rng.random(100) * 0.01
            cols.append(base_a + noise if i < 5 else base_b + noise)
        history = np.column_stack(cols)

        result = run_pca_diagnostic(history, _AGENT_NAMES)
        assert result.n_components_95pct < 4
        assert result.is_diversity_low is True

    def test_variance_ratios_are_native_float(self) -> None:
        """All variance ratios must be native ``float``."""
        history = _make_independent_signals(50)
        result = run_pca_diagnostic(history, _AGENT_NAMES)
        for r in result.explained_variance_ratios:
            assert type(r) is float

    def test_variance_ratios_sum_to_one(self) -> None:
        """Explained variance ratios should sum to ~1.0."""
        history = _make_independent_signals(50)
        result = run_pca_diagnostic(history, _AGENT_NAMES)
        assert abs(sum(result.explained_variance_ratios) - 1.0) < 1e-6

    def test_result_model_is_frozen(self) -> None:
        """PCADiagnosticResult should be immutable."""
        result = PCADiagnosticResult(
            n_components_95pct=5,
            explained_variance_ratios=[0.5, 0.3, 0.1, 0.05, 0.05],
            is_diversity_low=False,
        )
        with pytest.raises(Exception):
            result.n_components_95pct = 10  # type: ignore[misc]
