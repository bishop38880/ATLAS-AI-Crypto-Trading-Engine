"""Regime-Specific Weight Profiles.

Provides weight mapping based on the current market regime.
Regime weights are blended by the regime probabilities from the HMM.
"""

from __future__ import annotations

from atlas.ml.regime_detector import RegimeResult


# Base weights mapping (assuming standard uniform-ish distribution before multipliers)
# Actually, the requirements ask for regime multipliers applied to base weights.
# The `REGIME_WEIGHTS` table from requirements:
# Category     | Bull | Bear | Volatile
# TECHNICAL    | 1.2  | 0.8  | 0.6
# DERIVATIVES  | 0.8  | 1.3  | 1.4
# ONCHAIN      | 1.0  | 1.0  | 0.7
# SENTIMENT    | 1.1  | 0.9  | 0.5
# WHALE        | 0.9  | 1.2  | 1.3
# LIQUIDATION  | 0.7  | 1.3  | 1.5
# REGIME       | 1.0  | 1.0  | 1.0
# FUNDING      | 0.8  | 1.2  | 1.1
# NEWS_MACRO   | 1.0  | 1.0  | 1.0
# CORRELATION  | 1.0  | 1.0  | 1.0

REGIME_WEIGHTS: dict[str, dict[str, float]] = {
    "bull": {
        "technical": 1.2,
        "derivatives": 0.8,
        "onchain": 1.0,
        "sentiment": 1.1,
        "whale": 0.9,
        "liquidation": 0.7,
        "regime": 1.0,
        "funding": 0.8,
        "news_macro": 1.0,
        "correlation": 1.0,
        "macro": 1.0,
        "context": 1.0,
    },
    "bear": {
        "technical": 0.8,
        "derivatives": 1.3,
        "onchain": 1.0,
        "sentiment": 0.9,
        "whale": 1.2,
        "liquidation": 1.3,
        "regime": 1.0,
        "funding": 1.2,
        "news_macro": 1.0,
        "correlation": 1.0,
        "macro": 1.0,
        "context": 1.0,
    },
    "volatile": {
        "technical": 0.6,
        "derivatives": 1.4,
        "onchain": 0.7,
        "sentiment": 0.5,
        "whale": 1.3,
        "liquidation": 1.5,
        "regime": 1.0,
        "funding": 1.1,
        "news_macro": 1.0,
        "correlation": 1.0,
        "macro": 1.0,
        "context": 1.0,
    },
}

def get_regime_adjusted_weights(
    base_weights: dict[str, float],
    regime_result: RegimeResult,
) -> dict[str, float]:
    """Calculate blended weights given regime probabilities.
    
    Args:
        base_weights: Base weights mapping.
        regime_result: Current HMM regime result.
        
    Returns:
        Dict of blended, normalised weights (sum to 1.0).
    """
    blended: dict[str, float] = {}
    probs = regime_result.regime_probabilities
    
    for category, base_weight in base_weights.items():
        if category == "risk":
            # Risk is veto-only, ignore in weighted sum
            continue
            
        blended_weight = 0.0
        # Blend across all three regimes
        for regime in ["bull", "bear", "volatile"]:
            prob = probs.get(regime, 0.0)
            multiplier = REGIME_WEIGHTS[regime].get(category, 1.0)
            blended_weight += prob * base_weight * multiplier
            
        blended[category] = blended_weight
        
    # Normalise so they sum to 1.0
    total = sum(blended.values())
    if total > 0:
        return {k: float(v / total) for k, v in blended.items()}
    
    # Fallback to equal weighting if total is 0
    fallback_val = 1.0 / len(blended) if blended else 1.0
    return {k: float(fallback_val) for k in blended}
