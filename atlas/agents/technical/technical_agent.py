"""Technical Analyst — trend/volatility filter only (matches confluence pillar).

15-point tier (15/220): ADX for regime (trend vs chop) and Bollinger band boundary
context. RSI, MACD, and volume do not contribute to the score (may still appear in
sub-signals for LLM context).
"""

import time
from typing import Any
from loguru import logger

from atlas.agents.base import (
    AgentCategory,
    AgentResult,
    AgentTier,
    BaseAgent,
    SignalDirection,
)
from atlas.models.signal import SubSignalResult
from atlas.agents.base import AgentTelemetry

MAX_SCORE = 15


class TechnicalAgent(BaseAgent):
    """Evaluates technical structure and L2 orderbook liquidity."""

    @property
    def name(self) -> str:
        return "technical"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.TECHNICAL

    @property
    def tier(self) -> AgentTier:
        return AgentTier.ANALYST

    @staticmethod
    def _extract_float(data: dict[str, Any], col: str, default: float = 0.0) -> float:
        """Safely extract float metrics from the dictionary."""
        val = data.get(col)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    async def score(
        self, data: dict[str, Any], context: dict[str, Any] | None = None
    ) -> AgentResult:
        start_ms = time.monotonic()
        if not data:
            logger.error("technical_agent_data_error | error=empty dict")
            return self._build_empty_result()

        m = self._extract_technicals(data)
        pts = self._compute_technical_score(m)
        direction = self._determine_direction(m)
        sub_signals = self._build_sub_signals(m, direction)
        risks = self._compute_risks(m, direction)
        elapsed = (time.monotonic() - start_ms) * 1000
        return self._assemble_result(pts, direction, sub_signals, risks, elapsed)

    def _extract_technicals(self, data: dict[str, Any]) -> dict[str, float]:
        """Extract all technical metrics from the dictionary."""
        return {
            "vol_ratio": self._extract_float(data, "volume_ratio", 1.0),
            "rsi": self._extract_float(data, "rsi_14", 50.0),
            "adx": self._extract_float(data, "adx", 15.0),
            "macd": self._extract_float(data, "macd_histogram", 0.0),
            "bb_pos": self._extract_float(data, "bb_position", 0.5),
            "vwap_dev": self._extract_float(data, "vwap_deviation_pct", 0.0),
            "sr_res": self._extract_float(data, "sr_nearest_resistance_pct", 5.0),
            "sr_sup": self._extract_float(data, "sr_nearest_support_pct", -5.0),
            "obi": self._extract_float(data, "orderbook_imbalance", 0.0),
        }

    @staticmethod
    def _compute_technical_score(m: dict[str, float]) -> int:
        """ADX + Bollinger bands only — 15-point cap (aligned with confluence v3)."""
        # ADX: up to 8 pts — trending vs choppy
        if m["adx"] > 30.0:
            adx_pts = 8
        elif m["adx"] >= 20.0:
            adx_pts = 5
        else:
            adx_pts = 2
        # Bollinger position: up to 7 pts — boundary / squeeze context
        if m["bb_pos"] < 0.1 or m["bb_pos"] > 0.9:
            bb_pts = 7
        elif m["bb_pos"] < 0.25 or m["bb_pos"] > 0.75:
            bb_pts = 4
        else:
            bb_pts = 1
        return min(adx_pts + bb_pts, MAX_SCORE)

    @staticmethod
    def _determine_direction(m: dict[str, float]) -> SignalDirection:
        """Direction hints from ADX + band extremes only (filter, not alpha)."""
        adx_strong = m["adx"] >= 25.0
        if adx_strong and m["bb_pos"] < 0.15:
            return SignalDirection.BULLISH
        if adx_strong and m["bb_pos"] > 0.85:
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL

    @staticmethod
    def _build_sub_signals(
        m: dict[str, float], direction: SignalDirection,
    ) -> dict[str, SubSignalResult]:
        """Build standardized sub-signal output for DeepSeek."""
        return {
            "volume_ratio": SubSignalResult.model_construct(
                value="{:.2f}x".format(m["vol_ratio"]),
                flag="HIGH_VOL_BREAKOUT" if m["vol_ratio"] > 2.0 else "NORMAL",
            ),
            "rsi_14": SubSignalResult.model_construct(
                value="{:.1f}".format(m["rsi"]),
                flag="OVERSOLD" if m["rsi"] < 30 else "OVERBOUGHT" if m["rsi"] > 70 else "NEUTRAL",
            ),
            "trend_strength": SubSignalResult.model_construct(
                value="ADX:{:.1f} | BB:{:.2f}".format(m["adx"], m["bb_pos"]),
                flag="STRONG_TREND" if m["adx"] > 30 else "WEAK_TREND",
            ),
            "vwap_deviation": SubSignalResult.model_construct(
                value="{:+.2f}%".format(m["vwap_dev"]),
                flag="EXTREME_DEVIATION" if abs(m["vwap_dev"]) > 3.0 else "MEAN_REVERTING",
            ),
            "sr_distance": SubSignalResult.model_construct(
                value="Res: +{:.2f}% | Sup: {:.2f}%".format(m["sr_res"], m["sr_sup"]),
                flag="TERRIBLE_RR_WARNING" if (m["sr_res"] < 0.5 and direction == SignalDirection.BULLISH) else "CLEAR_PATH",
            ),
            "orderbook_imbalance": SubSignalResult.model_construct(
                value="{:+.2f}".format(m["obi"]),
                flag="LIMIT_SELL_WALL" if m["obi"] < -0.5 else "LIMIT_BUY_WALL" if m["obi"] > 0.5 else "BALANCED",
            ),
        }

    @staticmethod
    def _compute_risks(
        m: dict[str, float], direction: SignalDirection,
    ) -> list[str]:
        """Compute risk warnings based on metrics."""
        risks: list[str] = []
        if m["sr_res"] < 0.5 and direction == SignalDirection.BULLISH:
            risks.append("Immediate heavy resistance directly overhead. R/R is highly constrained.")
        if m["obi"] < -0.6 and direction == SignalDirection.BULLISH:
            risks.append("Aggressive limit sell wall detected immediately above current price.")
        return risks

    def _assemble_result(
        self, score: int, direction: SignalDirection,
        sub_signals: dict[str, SubSignalResult],
        risks: list[str], elapsed: float,
    ) -> AgentResult:
        """Assemble the final AgentResult."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=score,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=direction,
            explanation="Technical filter scores {}/{}. Trend is {}.".format(
                score, MAX_SCORE, direction.name,
            ),
            convergences=[],
            risks=risks,
            veto=False,
            sub_signals=sub_signals,
            telemetry=AgentTelemetry(latency_ms=elapsed),
        )

    def _build_empty_result(self) -> AgentResult:
        """Return zero-score result on data failure."""
        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=MAX_SCORE,
            weight=1.0,
            direction=SignalDirection.NEUTRAL,
            explanation="Failed to parse technical data",
            convergences=[],
            risks=["DATA_MISSING"],
            veto=False,
            sub_signals={},
            telemetry=AgentTelemetry(latency_ms=0.0),
        )
