import numpy as np

def project_weight_adjustments(raw: np.ndarray, clip: float = 0.1) -> np.ndarray:
    a = np.clip(raw, -clip, clip)
    a = a - a.mean()
    a = np.clip(a, -clip, clip)
    residual = a.sum()
    if abs(residual) > 1e-9:
        # We need to subtract the residual from the element that has the most slack.
        # This is the element with the SMALLEST absolute value.
        idx = int(np.argmin(np.abs(a)))
        a[idx] -= residual
        a = np.clip(a, -clip, clip)
    return a

def test_projection_is_deterministic_and_feasible():
    rng = np.random.default_rng(42)
    for _ in range(1000):
        raw = rng.uniform(-0.5, 0.5, size=5)
        adjusted = project_weight_adjustments(raw, clip=0.1)
        assert np.all(np.abs(adjusted) <= 0.1 + 1e-9), f"Failed bounds: {adjusted}"
        assert abs(adjusted.sum()) < 1e-6, f"Failed sum: {adjusted.sum()}"

test_projection_is_deterministic_and_feasible()
print("Success!")
