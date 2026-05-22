"""Gate score models — one timeframe gate output and full chain result."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class Regime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    TRANSITIONAL = "TRANSITIONAL"


class GateScore(BaseModel, frozen=True):
    """Confluence evaluation for a single timeframe gate."""

    timeframe: str = Field(description="daily | 4hr | 1hr | 15min | 5min")
    score: int = Field(ge=0, le=220, description="Raw confluence score")
    direction: Direction
    direction_confidence: float = Field(ge=0.0, le=1.0)
    regime: Regime

    derivatives_score: int = 0
    whale_score: int = 0
    technical_score: int = 0
    sentiment_score: int = 0
    macro_score: int = 0

    passed: bool = False
    fail_reason: str | None = None

    funding_rate: float | None = None
    oi_change_pct: float | None = None
    whale_netflow: float | None = None
    premium_pct: float | None = None

    def to_prompt_context(self) -> str:
        """Compact scalar summary for LLM prompts."""
        parts = [
            "{} | Score: {}/220 | Direction: {} ({:.0%} confidence) | Regime: {}".format(
                self.timeframe.upper(),
                self.score,
                self.direction.value,
                self.direction_confidence,
                self.regime.value,
            ),
            "Components: D={} W={} T={} S={} M={}".format(
                self.derivatives_score,
                self.whale_score,
                self.technical_score,
                self.sentiment_score,
                self.macro_score,
            ),
        ]
        if self.funding_rate is not None:
            parts.append("FR={:.4f}%".format(self.funding_rate))
        if self.oi_change_pct is not None:
            parts.append("OI Δ={:+.1f}%".format(self.oi_change_pct))
        if self.whale_netflow is not None:
            parts.append("Whale flow={:+,.0f}".format(self.whale_netflow))
        if self.premium_pct is not None:
            parts.append("CB premium={:+.3f}%".format(self.premium_pct))
        return " | ".join(parts)


class GateChainResult(BaseModel, frozen=True):
    """Full sequential gate chain outcome for one asset."""

    symbol: str
    gates: dict[str, GateScore] = Field(default_factory=dict)
    passed: bool = False
    final_score: int = 0
    final_direction: Direction = Direction.NEUTRAL
    timeframe_bonus: int = 0
    fail_at_gate: str | None = None
    fail_reason: str | None = None

    def to_prompt_context(self) -> str:
        lines = ["=== GATE CHAIN: {} ===".format(self.symbol)]
        for tf in ("daily", "4hr", "1hr", "15min", "5min"):
            gate = self.gates.get(tf)
            if gate is None:
                continue
            status = "PASS" if gate.passed else "FAIL"
            lines.append("  [{}] | {}".format(status, gate.to_prompt_context()))
        lines.append(
            "\nFINAL: Score={}/220 | Direction={} | TF Bonus={:+d}pts".format(
                self.final_score,
                self.final_direction.value,
                self.timeframe_bonus,
            ),
        )
        return "\n".join(lines)


def gate_score_from_cache(data: dict[str, Any], timeframe: str) -> GateScore:
    """Build a GateScore from a Redis JSON blob."""
    return GateScore(
        timeframe=timeframe,
        score=int(data["score"]),
        direction=Direction(str(data["direction"])),
        direction_confidence=float(data["direction_confidence"]),
        regime=Regime(str(data["regime"])),
        derivatives_score=int(data.get("derivatives_score", 0)),
        whale_score=int(data.get("whale_score", 0)),
        technical_score=int(data.get("technical_score", 0)),
        sentiment_score=int(data.get("sentiment_score", 0)),
        macro_score=int(data.get("macro_score", 0)),
        funding_rate=data.get("funding_rate"),
        oi_change_pct=data.get("oi_change_pct"),
        whale_netflow=data.get("whale_netflow"),
        premium_pct=data.get("premium_pct"),
    )
