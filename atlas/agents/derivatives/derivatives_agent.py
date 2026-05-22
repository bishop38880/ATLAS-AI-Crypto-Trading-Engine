"""Derivatives Analyst Agent — actual-value scoring with Z-score gates.

45-point tier (75/220 derivatives pillar share via orchestrator weights).
  - Funding Rate Z-score:        30 pts (non-linear gate, core squeeze fuel)
  - Open Interest Direction:     15 pts
  - Futures Basis:               10 pts
  Raw sum is capped at 45 to match the agent budget under the five-pillar system.
"""

import time

from typing import Any

from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentTelemetry,
    AgentTier,
    BaseAgent,
    SignalDirection,
)

MAX_SCORE = 45


class DerivativesAgent(BaseAgent):
    """Derivatives market condition scorer."""

    @property
    def name(self) -> str:
        return "derivatives"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.DERIVATIVES

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    @staticmethod
    def _extract_float(data: dict[str, Any], col: str) -> float:
        val = data.get(col)
        if val is None:
            return 0.0
        try:
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    @staticmethod
    def _extract_str(data: dict[str, Any], col: str) -> str:
        val = data.get(col)
        if val is None:
            return ""
        return str(val).strip().lower()

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Score derivatives market conditions from data."""
        start_ms = time.monotonic()
        if not data:
            return self._make_zero_result("No data available")
        return self._evaluate_metrics(data, start_ms)

    def _evaluate_metrics(self, data: dict[str, Any], start_ms: float) -> AgentResult:
        """Evaluate extracted metrics and compile the final AgentResult."""
        zscore = self._extract_float(data, "zscore")
        f_score = self._score_funding(zscore)
        f_exp = f"FUNDING: Z={zscore:.2f} → {f_score}pts"

        oi_chg = self._extract_float(data, "oi_change_4h")
        oi_trend = self._extract_str(data, "oi_trend")
        oi_score = self._score_open_interest(oi_chg, oi_trend)
        oi_exp = f"OI: {oi_trend} change={oi_chg:.1f}% → {oi_score}pts"

        b_ann = self._extract_float(data, "basis_annualised")
        b_sig = self._extract_str(data, "basis_signal")
        b_score = self._score_basis(b_ann, b_sig)
        b_exp = f"BASIS: {b_sig} ann={b_ann:.1f}% → {b_score}pts"

        total = min(int(f_score + oi_score + b_score), MAX_SCORE)
        elapsed = (time.monotonic() - start_ms) * 1000

        return AgentResult.model_construct(
            agent_name=self.name,
            score=total,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=self._determine_direction(int(total)),
            explanation=f"{f_exp}; {oi_exp}; {b_exp}",
            convergences=[], risks=[], veto=False,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )

    def _determine_direction(self, score: int) -> SignalDirection:
        if score >= 28:
            return SignalDirection.BULLISH
        if score <= 8:
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    def _score_funding(self, zscore: float) -> int:
        abs_z = abs(zscore)
        if abs_z >= 2.5:
            return 30
        if abs_z >= 1.5:
            return 15
        return 0

    def _score_open_interest(self, oi_change_4h: float, oi_trend: str) -> int:
        if oi_trend == "expanding" and oi_change_4h > 3.0:
            return 15
        if oi_trend == "expanding" and oi_change_4h > 1.0:
            return 8
        return 0

    def _score_basis(self, basis_annualised: float, basis_signal: str) -> int:
        if basis_signal == "backwardation":
            return 10
        if basis_signal == "contango" and basis_annualised > 30.0:
            return 10
        if basis_signal == "neutral":
            return 4
        return 0

