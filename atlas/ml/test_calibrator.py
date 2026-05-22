import msgspec
import numpy as np
import pytest
from atlas.ml.calibrator import ConvictionCalibrator

def test_calibrator_well_calibrated_synthetic_data():
    calibrator = ConvictionCalibrator()
    
    np.random.seed(42)
    raw_scores = np.linspace(0, 100, 1000)
    probabilities = raw_scores / 100.0
    outcomes = np.random.binomial(1, probabilities)
    
    result = calibrator.train(raw_scores, outcomes)
    
    assert result.ece < 0.05
    assert calibrator._fitted is True
    
    calibrated_val = calibrator.calibrate(80.0)
    assert isinstance(calibrated_val, float)
    assert 0.7 < calibrated_val < 0.9

def test_calibrator_poorly_calibrated_synthetic_data():
    calibrator = ConvictionCalibrator()
    
    np.random.seed(42)
    raw_scores = np.random.uniform(0, 100, 1000)
    # Regardless of score, actual prob is 0.2
    outcomes = np.random.binomial(1, 0.2, 1000)
    
    result = calibrator.train(raw_scores, outcomes)
    
    assert result.ece < 0.05
    
    calibrated_val = calibrator.calibrate(80.0)
    assert isinstance(calibrated_val, float)
    assert 0.1 < calibrated_val < 0.3

def test_calibrator_out_of_bounds_clip():
    calibrator = ConvictionCalibrator()
    raw_scores = np.array([10.0, 50.0, 90.0])
    outcomes = np.array([0, 1, 1])
    calibrator.train(raw_scores, outcomes)
    
    calibrated_val = calibrator.calibrate(150.0)
    assert calibrated_val <= 1.0
    calibrated_val_low = calibrator.calibrate(-50.0)
    assert calibrated_val_low >= 0.0

def test_calibrator_serialization_roundtrip():
    calibrator = ConvictionCalibrator()
    np.random.seed(42)
    raw_scores = np.linspace(0, 100, 100)
    probabilities = raw_scores / 100.0
    outcomes = np.random.binomial(1, probabilities)
    
    calibrator.train(raw_scores, outcomes)
    coeffs = calibrator.extract_coefficients()
    
    encoded = msgspec.json.encode(coeffs)
    decoded = msgspec.json.decode(encoded, type=type(coeffs))
    
    new_calibrator = ConvictionCalibrator()
    new_calibrator.load_coefficients(decoded)
    
    test_scores = [10.0, 50.0, 80.0, 95.0]
    for score in test_scores:
        assert calibrator.calibrate(score) == new_calibrator.calibrate(score)

def test_calibrator_not_fitted():
    calibrator = ConvictionCalibrator()
    assert calibrator.calibrate(85.0) == 0.85
