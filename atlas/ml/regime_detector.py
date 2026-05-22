"""HMM Regime Detector.

Uses a 3-state GaussianHMM to detect market regimes (Bull, Bear, Volatile)
based on log returns, volatility, and volume ratio.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from hmmlearn.hmm import GaussianHMM
from pydantic import BaseModel, Field


class RegimeResult(BaseModel, frozen=True):
    """Immutable result of HMM regime detection."""

    current_regime: str = Field(description="One of: 'bull', 'bear', 'volatile'")
    regime_probabilities: dict[str, float] = Field(description="Native float probs summing to ~1.0")
    regime_duration_bars: int = Field(default=1, description="How long the current regime has lasted")
    transition_probability: float = Field(default=0.0, description="Native float prob of transitioning")


class HMMRegimeDetector:
    """Detects market regimes using a Hidden Markov Model."""

    def __init__(self) -> None:
        """Initialize the HMM."""
        self._hmm = GaussianHMM(
            n_components=3,
            covariance_type="diag",
            n_iter=100,
            min_covar=1e-6,
        )
        self._is_fitted = False
        # Mapping from hidden state index to regime name
        self._state_map: dict[int, str] = {}

    def fit(self, features: np.ndarray) -> None:
        """Fit the HMM on historical features. CPU-bound.
        
        Args:
            features: shape (n_samples, 3).
                      Col 0: log returns, Col 1: volatility, Col 2: vol ratio.
        """
        self._hmm.fit(features)
        self._is_fitted = True
        self._map_states_to_regimes()

    def _map_states_to_regimes(self) -> None:
        """Map the 3 hidden states to Bull, Bear, and Volatile."""
        means = self._hmm.means_
        # Means shape is (3, 3) where columns are (return, volatility, vol_ratio)
        returns = means[:, 0]
        vols = means[:, 1]
        
        # Highest volatility is 'volatile'
        vol_idx = int(np.argmax(vols))
        
        remaining = [i for i in range(3) if i != vol_idx]
        
        # Among the remaining two, highest return is 'bull', lowest is 'bear'
        if returns[remaining[0]] > returns[remaining[1]]:
            bull_idx, bear_idx = remaining[0], remaining[1]
        else:
            bull_idx, bear_idx = remaining[1], remaining[0]
            
        self._state_map[bull_idx] = "bull"
        self._state_map[bear_idx] = "bear"
        self._state_map[vol_idx] = "volatile"

    def detect_regime(self, features: np.ndarray) -> RegimeResult:
        """Detect current regime from latest features. CPU-bound."""
        if not self._is_fitted:
            return _build_neutral_regime()

        states = self._hmm.predict(features)
        current_state = int(states[-1])
        current_regime = self._state_map.get(current_state, "volatile")
        duration = _calculate_duration(states)
        regime_probs = self._get_regime_probabilities(features)

        trans_mat = self._hmm.transmat_
        prob_transition = 1.0 - float(trans_mat[current_state, current_state])

        return RegimeResult(
            current_regime=current_regime,
            regime_probabilities=regime_probs,
            regime_duration_bars=duration,
            transition_probability=prob_transition,
        )

    def _get_regime_probabilities(
        self, features: np.ndarray,
    ) -> dict[str, float]:
        """Map HMM state probabilities to regime names."""
        log_probs = self._hmm.predict_proba(features)
        latest_probs = log_probs[-1]
        return {
            self._state_map[i]: float(latest_probs[i])
            for i in range(3)
            if i in self._state_map
        }


def _build_neutral_regime() -> RegimeResult:
    """Build a neutral fallback regime when unfitted."""
    return RegimeResult(
        current_regime="volatile",
        regime_probabilities={"bull": 0.33, "bear": 0.33, "volatile": 0.34},
        regime_duration_bars=1,
        transition_probability=0.0,
    )


def _calculate_duration(states: np.ndarray) -> int:
    """Calculate how many consecutive bars we've been in the current state."""
    if len(states) == 0:
        return 1
    current = states[-1]
    diffs = np.where(states != current)[0]
    if len(diffs) == 0:
        return len(states)
    return len(states) - 1 - int(diffs[-1])
