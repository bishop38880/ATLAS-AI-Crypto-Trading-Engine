import pytest
from atlas.ml.calibrator import CalibrationBin, CalibrationResult
from atlas.ml.reliability_diagram import generate_reliability_diagram_svg

def test_reliability_diagram_svg_generation():
    bins = [
        CalibrationBin(bin_lower=0.0, bin_upper=0.2, predicted_mean=0.1, observed_rate=0.12, n_samples=100),
        CalibrationBin(bin_lower=0.2, bin_upper=0.4, predicted_mean=0.3, observed_rate=0.35, n_samples=150),
        CalibrationBin(bin_lower=0.4, bin_upper=0.6, predicted_mean=0.5, observed_rate=0.48, n_samples=200),
        CalibrationBin(bin_lower=0.6, bin_upper=0.8, predicted_mean=0.7, observed_rate=0.65, n_samples=50),
        CalibrationBin(bin_lower=0.8, bin_upper=1.0, predicted_mean=0.9, observed_rate=0.92, n_samples=20),
    ]
    result = CalibrationResult(ece=0.042, bins=bins, n_samples=520)
    
    svg = generate_reliability_diagram_svg(result)
    
    assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "</svg>" in svg
    assert "ECE: 0.0420" in svg
    
    circle_count = svg.count("<circle")
    assert circle_count == 5

def test_reliability_diagram_empty_bins():
    bins = [
        CalibrationBin(bin_lower=0.0, bin_upper=0.5, predicted_mean=0.0, observed_rate=0.0, n_samples=0),
        CalibrationBin(bin_lower=0.5, bin_upper=1.0, predicted_mean=0.8, observed_rate=0.8, n_samples=10),
    ]
    result = CalibrationResult(ece=0.0, bins=bins, n_samples=10)
    
    svg = generate_reliability_diagram_svg(result)
    
    circle_count = svg.count("<circle")
    assert circle_count == 1
