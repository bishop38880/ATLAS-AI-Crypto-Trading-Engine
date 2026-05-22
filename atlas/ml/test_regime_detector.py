"""Tests for HMM Regime Detector."""

import numpy as np
import pytest

from atlas.ml.regime_detector import HMMRegimeDetector, RegimeResult


@pytest.fixture
def dummy_features() -> np.ndarray:
    """Create dummy features representing Bull, Bear, and Volatile regimes."""
    # 90 samples, 3 features: (returns, volatility, vol_ratio)
    np.random.seed(42)
    
    # Bull: high returns, moderate vol
    bull = np.random.normal(loc=[0.05, 0.02, 1.0], scale=[0.01, 0.005, 0.1], size=(30, 3))
    
    # Bear: negative returns, moderate vol
    bear = np.random.normal(loc=[-0.05, 0.025, 1.2], scale=[0.01, 0.005, 0.1], size=(30, 3))
    
    # Volatile: near-zero returns, high vol
    volatile = np.random.normal(loc=[0.0, 0.08, 1.5], scale=[0.02, 0.01, 0.2], size=(30, 3))
    
    return np.vstack([bull, bear, volatile])


def test_regime_detector_unfitted() -> None:
    """Test detector returns neutral regime when unfitted."""
    detector = HMMRegimeDetector()
    features = np.array([[0.0, 0.0, 0.0]])
    
    result = detector.detect_regime(features)
    
    assert result.current_regime == "volatile"
    assert "bull" in result.regime_probabilities
    assert sum(result.regime_probabilities.values()) == pytest.approx(1.0)
    assert result.regime_duration_bars == 1
    assert result.transition_probability == 0.0


def test_regime_detector_fit_and_detect(dummy_features: np.ndarray) -> None:
    """Test full fit and detect cycle on dummy data."""
    detector = HMMRegimeDetector()
    
    # Fit the HMM
    detector.fit(dummy_features)
    assert detector._is_fitted is True
    assert len(detector._state_map) == 3
    assert set(detector._state_map.values()) == {"bull", "bear", "volatile"}
    
    # Detect regime
    # Last chunk is volatile
    result = detector.detect_regime(dummy_features)
    
    assert isinstance(result, RegimeResult)
    assert result.current_regime in {"bull", "bear", "volatile"}
    
    # Check types and properties
    probs = result.regime_probabilities
    assert isinstance(probs, dict)
    assert set(probs.keys()) == {"bull", "bear", "volatile"}
    
    # Ensure they are native floats, not np.float64
    for val in probs.values():
        assert isinstance(val, float)
        assert type(val) is float
        
    assert sum(probs.values()) == pytest.approx(1.0)
    assert isinstance(result.transition_probability, float)
    assert type(result.transition_probability) is float
    assert result.regime_duration_bars >= 1
