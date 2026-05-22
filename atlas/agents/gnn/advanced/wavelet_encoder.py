"""DWT Feature extraction for multi-scale temporal awareness."""

import torch
from loguru import logger

try:
    import pywt

    HAS_PYWT = True
except ImportError:
    HAS_PYWT = False


def extract_wavelet_features(time_series: list[float]) -> torch.Tensor:
    """Extract multi-scale features using DWT (Daubechies 4).

    Input: 1-D time series (e.g., 128-length array).
    Output: 48-dim feature vector tensor of float32.

    If pywt is unavailable, returns zeros and logs a warning.
    """
    if not HAS_PYWT:
        logger.warning("pywavelets unavailable; wavelet features zeroed")
        return torch.zeros(48, dtype=torch.float32)

    try:
        # Decompose using 'db4' at level 3.
        # wavedec returns [cA3, cD3, cD2, cD1]
        coeffs = pywt.wavedec(time_series, "db4", level=3)  # type: ignore[reportPossiblyUnboundVariable]
        
        # Extract detail coefficients: [cD1, cD2, cD3]
        # In wavedec, coeffs[1:] is [cD3, cD2, cD1]. Reverse to get cD1, cD2, cD3
        detail_coeffs = coeffs[1:]

        concatenated = []
        for c in detail_coeffs[::-1]:
            concatenated.extend(c)

        features = concatenated[:48]

        # Pad if less than 48
        if len(features) < 48:
            features.extend([0.0] * (48 - len(features)))

        return torch.tensor(features, dtype=torch.float32)

    except Exception as e:
        logger.error("wavelet_extraction_failed | error={}", e)
        return torch.zeros(48, dtype=torch.float32)
