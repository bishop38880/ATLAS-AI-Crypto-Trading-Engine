"""Safety Layer Risk Agent — Veto-only gate.

This agent evaluates portfolio risk and system-wide market conditions.
It produces a boolean veto flag. If veto=True, the system forces a
No Position decision. It contributes exactly 0 points to the confluence
score (max_points=0).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation

import msgspec
from loguru import logger
import redis.asyncio as redis

import typing
from typing import Any

from atlas.agents.base import AgentCategory, AgentResult, BaseAgent, SignalDirection, AgentTier
from atlas.providers.hydra.listener import HydraStreamListener


class RiskAgent(BaseAgent):
    """Risk & Portfolio Management agent.

    A pure veto gate. Does not contribute points to the score.
    """

    def __init__(self, redis_client: redis.Redis, hydra: HydraStreamListener) -> None:
        """Initialize with Redis client and Hydra listener.

        Args:
            redis_client: An async Redis client instance.
            hydra: Hydra stream listener for cascade detection.
        """
        super().__init__()
        self._redis = redis_client
        self._hydra = hydra

    async def _read_decimal(self, key: str, default: str = "0") -> Decimal:
        """Read Decimal from Redis bytes safely."""
        raw: bytes | None = await self._redis.get(key)
        if raw is None:
            return Decimal(default)
        try:
            return Decimal(raw.decode("utf-8"))
        except (InvalidOperation, UnicodeDecodeError) as exc:
            logger.warning("risk_context_decimal_parse_failed | key={} | err={}", key, str(exc))
            return Decimal(default)

    async def _read_int(self, key: str, default: int = 0) -> int:
        """Read integer from Redis bytes safely."""
        raw: bytes | None = await self._redis.get(key)
        if raw is None:
            return default
        try:
            return int(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return default

    async def _read_str(self, key: str, default: str = "") -> str:
        """Read string from Redis bytes safely."""
        raw: bytes | None = await self._redis.get(key)
        return raw.decode("utf-8") if raw else default

    async def _read_json_list(self, key: str) -> list:
        """Read JSON list from Redis bytes safely."""
        raw: bytes | None = await self._redis.get(key)
        if raw is None:
            return []
        try:
            return msgspec.json.decode(raw)
        except msgspec.DecodeError:
            return []

    async def _eval_drawdown(self, risks: list[str]) -> bool:
        """Check for trailing PnL drawdown and trigger shutdowns."""
        pnl_1h = await self._read_decimal("portfolio:pnl_1h", "0")
        pnl_4h = await self._read_decimal("portfolio:pnl_4h", "0")
        pnl_24h = await self._read_decimal("portfolio:pnl_24h", "0")
        
        if pnl_24h < Decimal("-0.10"):
            risks.append("24h PnL < -10%: DRAWDOWN_SHUTDOWN")
            await self._redis.publish("system:kill_switch", "DRAWDOWN_SHUTDOWN")
            return True
            
        if pnl_4h < Decimal("-0.05"):
            risks.append("4h PnL < -5%: VETO ALL")
            return True
            
        if pnl_1h < Decimal("-0.03"):
            risks.append("1h PnL < -3%: VETO INCREASE")
            # Returns True to veto new positions entirely for now
            return True
            
        return False

    async def _eval_exposure(self, risks: list[str]) -> None:
        """Check exposure > 80%."""
        exposure = await self._read_decimal("portfolio:total_exposure_pct", "0")
        if exposure > Decimal("0.8"):
            risks.append("Exposure > 80%: halve suggested position size")

    async def _eval_correlation(self, asset: str, risks: list[str]) -> None:
        """Check correlation risk."""
        positions = await self._read_json_list("portfolio:open_positions")
        
        # In a real implementation this would check correlation matrix
        # For now we apply the rules generically
        if len(positions) >= 3:
            risks.append("3+ positions open: consider reducing position size by 50%")
            
        corr_val = await self._read_decimal(f"correlation:{asset}:max", "0")
        if corr_val > Decimal("0.8"):
            risks.append("Correlation > 0.8 with existing position: halve effective position size")

    async def _eval_volatility(self, risks: list[str]) -> None:
        """Check volatility regime."""
        regime = await self._read_str("market:volatility_regime", "unknown")
        if regime == "high_volatility":
            risks.append("High volatility regime: elevated risk")

    async def _eval_streak(self, risks: list[str]) -> None:
        """Check for consecutive losses or overtrading."""
        losses = await self._read_int("portfolio:consecutive_losses", 0)
        if losses >= 5:
            risks.append("5 consecutive losses: reduce position size by 50%")
            
        trades = await self._read_int("portfolio:recent_trades", 0)
        if trades > 10:
            risks.append("High trade frequency: potential overtrading")

    async def _eval_cascade(self, asset: str, risks: list[str]) -> bool:
        """Check for HYDRA Tier-4 cascade (FAST-PATH OVERRIDE VETO)."""
        event = self._hydra.get_latest_event(asset)
        if event is None and "/" in asset:
            base = asset.split("/", maxsplit=1)[0].strip()
            if base:
                event = self._hydra.get_latest_event(base)
        if event is not None and event.tier >= 4:
            risks.append("Tier-4 cascade occurring: FAST-PATH VETO")
            return True
        return False

    @property
    def name(self) -> str:
        return "risk"

    @property
    def category(self) -> AgentCategory:
        return AgentCategory.RISK

    @property
    def tier(self) -> AgentTier:
        return AgentTier.RISK

    async def score(
        self,
        data: dict[str, Any],
        context: dict[str, typing.Any] | None = None,
    ) -> AgentResult:
        """Evaluate all risk dimensions and produce a veto flag."""
        context = context or {}
        asset = context.get("asset", "")

        risks: list[str] = []
        veto = await self._gather_risk_evals(asset, risks)

        if not veto and len(risks) == 0:
            logger.info("risk_agent_healthy | asset={}", asset)

        return AgentResult.model_construct(
            agent_name=self.name,
            score=0,
            max_score=0,
            weight=0.0,
            direction=SignalDirection.NEUTRAL,
            explanation="Risk agent evaluation complete",
            convergences=[],
            risks=risks,
            veto=veto,
        )

    async def _gather_risk_evals(
        self, asset: str, risks: list[str],
    ) -> bool:
        """Run all risk evaluators concurrently. Returns veto flag."""
        drawdown_veto = False
        cascade_veto = False

        async def _run_drawdown() -> None:
            nonlocal drawdown_veto
            drawdown_veto = await self._eval_drawdown(risks)

        async def _run_cascade() -> None:
            nonlocal cascade_veto
            cascade_veto = await self._eval_cascade(asset, risks)

        await asyncio.gather(
            _run_drawdown(),
            self._eval_exposure(risks),
            self._eval_correlation(asset, risks),
            self._eval_volatility(risks),
            self._eval_streak(risks),
            _run_cascade(),
        )
        return drawdown_veto or cascade_veto


