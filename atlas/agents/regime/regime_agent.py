"""Market Regime Agent.

Uses HMMRegimeDetector to classify the market state based on OHLCV history.
Outputs a Neutral direction but includes the RegimeResult in its sub-signals.
"""

from __future__ import annotations

import asyncio
from typing import Any

import numpy as np
from loguru import logger

from atlas.agents.base import AgentCategory, AgentResult, BaseAgent, SignalDirection, AgentTier
from atlas.ml.regime_detector import HMMRegimeDetector, RegimeResult

MAX_SCORE = 12


class MarketRegimeAgent(BaseAgent):
    """Detects market regime (Bull, Bear, Volatile) via Hidden Markov Model."""

    MIN_SAMPLES_TO_EMIT: int = 30  # HMM requires ≥30 samples per Gaussian

    def __init__(self) -> None:
        """Initialize the Regime agent and its internal detector."""
        super().__init__()
        self._detector = HMMRegimeDetector()
        
    @property
    def name(self) -> str:
        return "regime"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.CONTEXT

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    async def score(
        self,
        data: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Evaluate data and produce an AgentResult with regime data.
        
        Args:
            data: dict containing relevant market data. 
            context: Additional context.
            
        Returns:
            An AgentResult with regime info in sub_signals.
        """
        try:
            ctx = context or {}
            close = np.asarray(ctx.get("close", []), dtype=np.float64)
            volume = np.asarray(ctx.get("volume", []), dtype=np.float64)
            if len(close) < 90:
                logger.warning("regime_agent_insufficient_bars | bars={}", len(close))
                return self._make_zero_result("insufficient data for regime detection")
                
            features = await asyncio.to_thread(self._compute_features, close, volume)
            
            # Fit and detect must be off the event loop
            await asyncio.to_thread(self._detector.fit, features)
            regime_result: RegimeResult = await asyncio.to_thread(
                self._detector.detect_regime, features
            )
            
            return self._build_regime_result(regime_result)
        except Exception as e:
            logger.error("regime_agent_failed | error={}", str(e))
            return self._make_zero_result("error during detection: {}".format(str(e)))

    def _build_regime_result(self, regime_result: RegimeResult) -> AgentResult:
        """Helper to construct the final AgentResult payload."""
        explanation = (
            f"Regime is {regime_result.current_regime.upper()} "
            f"(duration: {regime_result.regime_duration_bars} bars)"
        )
        sub_signals = {
            "regime": regime_result.current_regime,
            "probabilities": regime_result.regime_probabilities,
            "duration": regime_result.regime_duration_bars,
            "transition_prob": regime_result.transition_probability,
        }
        score = self._calculate_regime_score(regime_result)
        direction = self._calculate_regime_direction(regime_result)
        return AgentResult.model_construct(
            agent_name=self.name,
            score=score,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation=explanation,
            convergences=[f"market is in {regime_result.current_regime} regime"],
            risks=[],
            veto=False,
            sub_signals=sub_signals,
        )

    def _calculate_regime_score(self, regime_result: RegimeResult) -> int:
        """Convert regime confidence into visible context points."""
        current_probability = regime_result.regime_probabilities.get(
            regime_result.current_regime,
            0.0,
        )
        transition_penalty = 1.0 - regime_result.transition_probability
        confidence_score = current_probability * transition_penalty
        raw = confidence_score * float(MAX_SCORE)
        return int(max(0.0, min(float(MAX_SCORE), raw)))

    def _calculate_regime_direction(
        self,
        regime_result: RegimeResult,
    ) -> SignalDirection:
        """Map the detected market regime to directional context."""
        if regime_result.current_regime == "bull":
            return SignalDirection.BULLISH
        if regime_result.current_regime == "bear":
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _compute_features(
        self, close: np.ndarray, volume: np.ndarray,
    ) -> np.ndarray:
        """Compute log-returns, rolling volatility, and vol ratio via numpy."""
        log_returns = np.diff(np.log(close))                       # (N-1,)
        n = len(log_returns)
        win = 20
        # Rolling std of log_returns
        volatility = np.full(n, np.nan)
        for i in range(win - 1, n):
            volatility[i] = float(np.std(log_returns[i - win + 1 : i + 1], ddof=1))
        # Rolling mean of volume (trim volume to match log_returns length)
        vol = volume[1:]
        vol_sma = np.full(n, np.nan)
        for i in range(win - 1, n):
            vol_sma[i] = float(np.mean(vol[i - win + 1 : i + 1]))
        vol_ratio = np.where(vol_sma > 0, vol / vol_sma, 1.0)
        # Stack and drop rows where any column is NaN (first 19 rows)
        stacked = np.column_stack([log_returns, volatility, vol_ratio])
        features = stacked[~np.any(np.isnan(stacked), axis=1)]
        if len(features) == 0:
            return features

        # Deterministic fallback data can be too regular for full-covariance HMMs.
        # Tiny column-specific jitter keeps covariance positive-definite without
        # changing the economic interpretation of returns, volatility, or volume.
        jitter = np.linspace(-1e-6, 1e-6, len(features), dtype=np.float64)
        features[:, 0] = features[:, 0] + jitter
        features[:, 1] = features[:, 1] + jitter * 0.1
        features[:, 2] = features[:, 2] + jitter * 10.0
        return features
