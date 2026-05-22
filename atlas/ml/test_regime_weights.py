"""Tests for Regime Weight adjustments."""

import pytest

from atlas.ml.regime_detector import RegimeResult
from atlas.ml.regime_weights import get_regime_adjusted_weights


def test_bull_regime_upweights_technical() -> None:
    """Test bull regime probabilities properly upweight technical factor."""
    base = {
        "technical": 1.0,
        "derivatives": 1.0,
        "liquidation": 1.0,
    }
    
    bull_regime = RegimeResult(
        current_regime="bull",
        regime_probabilities={"bull": 1.0, "bear": 0.0, "volatile": 0.0},
        regime_duration_bars=5,
        transition_probability=0.01,
    )
    
    blended = get_regime_adjusted_weights(base, bull_regime)
    
    # Blended should sum to 1.0
    assert sum(blended.values()) == pytest.approx(1.0)
    
    # Technical should have highest weight because bull upweights it to 1.2
    # Derivatives down to 0.8, liquidation down to 0.7
    # 1.2 + 0.8 + 0.7 = 2.7. technical = 1.2 / 2.7 = 0.444
    assert blended["technical"] > blended["derivatives"]
    assert blended["technical"] > blended["liquidation"]
    assert blended["technical"] == pytest.approx(1.2 / 2.7)


def test_volatile_regime_upweights_derivatives_liquidation() -> None:
    """Test volatile regime probabilities upweight derivatives and liquidation."""
    base = {
        "technical": 1.0,
        "derivatives": 1.0,
        "liquidation": 1.0,
    }
    
    volatile_regime = RegimeResult(
        current_regime="volatile",
        regime_probabilities={"bull": 0.0, "bear": 0.0, "volatile": 1.0},
        regime_duration_bars=5,
        transition_probability=0.01,
    )
    
    blended = get_regime_adjusted_weights(base, volatile_regime)
    
    assert sum(blended.values()) == pytest.approx(1.0)
    
    # Volatile: Tech 0.6, Deriv 1.4, Liq 1.5. Total = 3.5.
    assert blended["liquidation"] == pytest.approx(1.5 / 3.5)
    assert blended["derivatives"] == pytest.approx(1.4 / 3.5)
    assert blended["technical"] == pytest.approx(0.6 / 3.5)
    assert blended["liquidation"] > blended["derivatives"] > blended["technical"]


def test_blended_weights_sum_to_one_across_mixed_probabilities() -> None:
    """Test weights correctly normalise when regime probabilities are mixed."""
    base = {
        "technical": 0.4,
        "derivatives": 0.3,
        "liquidation": 0.3,
    }
    
    mixed_regime = RegimeResult(
        current_regime="bear",
        regime_probabilities={"bull": 0.2, "bear": 0.6, "volatile": 0.2},
        regime_duration_bars=5,
        transition_probability=0.01,
    )
    
    blended = get_regime_adjusted_weights(base, mixed_regime)
    assert sum(blended.values()) == pytest.approx(1.0)
    
    # Test values are all float types (no np.float64)
    for val in blended.values():
        assert isinstance(val, float)
