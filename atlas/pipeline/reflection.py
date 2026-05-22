"""Reflection Critic engine with DEQ and WBFT guards."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Protocol

import asyncpg
import redis.asyncio as redis
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from atlas.core.llm_client import BaseLLMClient, LLMProviderError
from atlas.models.signal import SignalOutput
from atlas.shared.config import PolarisSettings


class WBFTAgentWeight(BaseModel):
    """Normalized WBFT weight per agent."""

    model_config = ConfigDict(frozen=True)
    agent_name: str
    sharpe: float = 0.0
    quality: float = 0.8
    timeout_penalty: float = 0.0
    weight: float = Field(ge=0.0, le=1.0)


class DEQCheckpoint(BaseModel):
    """One DEQ checkpoint output."""

    model_config = ConfigDict(frozen=True)
    round_idx: int = Field(ge=0)
    adjustment: int = Field(le=0)
    final_score: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0.0, le=1.0)
    wbft_score: float = Field(ge=0.0, le=100.0)
    reason: str
    elapsed_ms: int = Field(ge=0)


class ReflectionResult(BaseModel):
    """Result from reflection critic."""

    model_config = ConfigDict(frozen=True)
    skipped: bool = False
    timed_out: bool = False
    wbft_diverged: bool = False
    selected_round: int = 0
    selected_score: int = Field(ge=0, le=100)
    should_human_review: bool = False
    checkpoints: tuple[DEQCheckpoint, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class ReflectionEnvelope:
    """Container used by orchestrator integration."""

    signal: SignalOutput
    result: ReflectionResult


class SupportsHumanReviewQueue(Protocol):
    """Protocol for optional queue writer adapters."""

    async def write(self, payload: dict[str, Any]) -> None:
        """Write human review payload."""


class WBFTWeighter:
    """Computes normalized WBFT agent weights from Redis."""

    def __init__(self, redis_client: redis.Redis) -> None:
        self._redis = redis_client

    async def compute(self, agents: list[str]) -> list[WBFTAgentWeight]:
        raw = [await self._read_agent(name) for name in agents]
        total = sum(max(x.weight, 0.0) for x in raw) or 1.0
        return [x.model_copy(update={"weight": x.weight / total}) for x in raw]

    async def _read_agent(self, name: str) -> WBFTAgentWeight:
        key = f"wbft:agent:{name}"
        sharpe = await self._read_float(f"{key}:sharpe", default=0.0)
        quality = await self._read_float(f"{key}:quality", default=0.8)
        penalty = await self._read_float(f"{key}:timeout", default=0.0)
        weight = max(0.0, quality + sharpe - penalty)
        return WBFTAgentWeight(
            agent_name=name,
            sharpe=sharpe,
            quality=quality,
            timeout_penalty=penalty,
            weight=weight,
        )

    async def _read_float(self, key: str, default: float) -> float:
        value = await self._redis.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


class DEQSolver:
    """Deterministic equilibrium guard for reflection loops."""

    def __init__(self, settings: PolarisSettings) -> None:
        self._settings = settings

    def should_run_next_round(
        self,
        checkpoints: list[DEQCheckpoint],
        elapsed: float,
    ) -> tuple[bool, str]:
        if elapsed >= self._settings.reflection.deq_timeout_s:
            return False, "timeout"
        if len(checkpoints) >= self._settings.reflection.deq_max_rounds:
            return False, "max_rounds"
        if len(checkpoints) < 2:
            return True, "continue"
        delta = measure_convergence(checkpoints[-2], checkpoints[-1])
        if delta <= self._settings.reflection.convergence_pts:
            return False, "converged"
        return True, "continue"


def measure_convergence(previous: DEQCheckpoint, current: DEQCheckpoint) -> int:
    """Absolute score gap between two checkpoints."""

    return abs(previous.final_score - current.final_score)


def measure_wbft_divergence(round_one: DEQCheckpoint, round_two: DEQCheckpoint) -> int:
    """Difference in WBFT score between rounds."""

    return int(abs(round_one.wbft_score - round_two.wbft_score))


class ReflectionCritic:
    """Runs bounded reflection rounds without mutating confluence inputs."""

    def __init__(
        self,
        settings: PolarisSettings,
        llm_client: BaseLLMClient,
        redis_client: redis.Redis,
        pg_pool: asyncpg.Pool | None = None,
        queue_adapter: SupportsHumanReviewQueue | None = None,
    ) -> None:
        self._settings = settings
        self._llm_client = llm_client
        self._redis = redis_client
        self._pg_pool = pg_pool
        self._queue_adapter = queue_adapter
        self._weighter = WBFTWeighter(redis_client)
        self._deq_solver = DEQSolver(settings)

    async def reflect(self, signal: SignalOutput, context: dict[str, Any]) -> ReflectionEnvelope:
        if self._should_skip_reflection(signal, context):
            return ReflectionEnvelope(signal=signal, result=self._skip_result(signal))
        started = asyncio.get_running_loop().time()
        try:
            checkpoints = await self._run_rounds(signal, context, started)
        except (TimeoutError, asyncio.TimeoutError, LLMProviderError):
            fallback = ReflectionResult(
                timed_out=True,
                selected_round=0,
                selected_score=signal.score,
                reason="timeout",
            )
            return ReflectionEnvelope(signal=signal, result=fallback)
        result = await self._finalize_result(signal, checkpoints)
        updated = self._apply_reflection_to_signal(signal, result)
        return ReflectionEnvelope(signal=updated, result=result)

    def _should_skip_reflection(self, signal: SignalOutput, context: dict[str, Any]) -> bool:
        if not self._settings.reflection.enabled:
            return True
        if signal.raw_confluence_score < self._settings.reflection.score_threshold:
            return True
        return self._drawdown_exceeded(context)

    def _drawdown_exceeded(self, context: dict[str, Any]) -> bool:
        drawdown = context.get("portfolio_drawdown_4h_pct")
        return bool(drawdown is not None and float(drawdown) > 5.0)

    def _skip_result(self, signal: SignalOutput) -> ReflectionResult:
        return ReflectionResult(
            skipped=True,
            selected_round=0,
            selected_score=signal.score,
            reason="disabled_or_threshold_or_drawdown",
        )

    async def _run_rounds(
        self,
        signal: SignalOutput,
        context: dict[str, Any],
        started: float,
    ) -> list[DEQCheckpoint]:
        checkpoints: list[DEQCheckpoint] = []
        agents = sorted(signal.agent_breakdown.keys())
        weights = await self._weighter.compute(agents=agents)
        while True:
            nxt = len(checkpoints) + 1
            checkpoint = await self._run_round(signal, context, weights, nxt, started)
            checkpoints.append(checkpoint)
            run_more, _ = self._deq_solver.should_run_next_round(
                checkpoints, asyncio.get_running_loop().time() - started,
            )
            if not run_more:
                return checkpoints

    async def _run_round(
        self,
        signal: SignalOutput,
        context: dict[str, Any],
        weights: list[WBFTAgentWeight],
        round_idx: int,
        started: float,
    ) -> DEQCheckpoint:
        prompt = self._build_reflection_prompt(signal, context, weights, round_idx)
        timeout = self._settings.reflection.deq_timeout_s
        llm = await asyncio.wait_for(self._llm_client.complete(prompt), timeout=timeout)
        adjustment = self._parse_reflection_output(llm.text)
        score = _clamp_score(signal.score + adjustment, self._settings.reflection.min_score_floor)
        return DEQCheckpoint(
            round_idx=round_idx,
            adjustment=adjustment,
            final_score=score,
            confidence=max(0.0, min(1.0, 1.0 - (abs(adjustment) / 100.0))),
            wbft_score=_round_wbft_score(weights, adjustment),
            reason="ok",
            elapsed_ms=int((asyncio.get_running_loop().time() - started) * 1000),
        )

    def _build_reflection_prompt(
        self,
        signal: SignalOutput,
        context: dict[str, Any],
        weights: list[WBFTAgentWeight],
        round_idx: int,
    ) -> str:
        wbft = ", ".join(f"{w.agent_name}:{w.weight:.3f}" for w in weights)
        return (
            "You are ReflectionCritic.\n"
            f"Round={round_idx} Model={self._settings.reflection.model}\n"
            f"Primary synthesis: {signal.reasoning_summary}\n"
            f"Adversarial flags: {','.join(signal.key_risks)}\n"
            f"WBFT weights: {wbft}\n"
            f"Context: {context}\n"
            "Return CONFIDENCE_ADJUSTMENT=<int> in [-15,0]."
        )

    def _parse_reflection_output(self, text: str) -> int:
        marker = "CONFIDENCE_ADJUSTMENT="
        for line in text.splitlines():
            if marker in line:
                raw = line.split(marker, 1)[1].strip().split()[0]
                return _clamp_adjustment(int(raw), self._settings.reflection.max_score_reduction)
        return 0

    async def _finalize_result(
        self,
        signal: SignalOutput,
        checkpoints: list[DEQCheckpoint],
    ) -> ReflectionResult:
        if not checkpoints:
            return ReflectionResult(
                timed_out=True, selected_round=0, selected_score=signal.score, reason="timeout",
            )
        selected = self._select_final_checkpoint(checkpoints)
        diverged = self._wbft_diverged(checkpoints)
        human = diverged
        if human:
            await self._write_human_review_queue(signal, checkpoints)
        return ReflectionResult(
            selected_round=selected.round_idx,
            selected_score=selected.final_score,
            wbft_diverged=diverged,
            should_human_review=human,
            checkpoints=tuple(checkpoints),
            reason="ok",
        )

    def _select_final_checkpoint(self, checkpoints: list[DEQCheckpoint]) -> DEQCheckpoint:
        if self._wbft_diverged(checkpoints):
            return checkpoints[0]
        return checkpoints[-1]

    def _wbft_diverged(self, checkpoints: list[DEQCheckpoint]) -> bool:
        if len(checkpoints) < 2:
            return False
        divergence = measure_wbft_divergence(checkpoints[0], checkpoints[1])
        return divergence > self._settings.reflection.wbft_divergence_pts

    async def _write_human_review_queue(
        self,
        signal: SignalOutput,
        checkpoints: list[DEQCheckpoint],
    ) -> None:
        payload = {
            "signal_id": signal.signal_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": "wbft_divergence",
            "checkpoints": [c.model_dump(mode="json") for c in checkpoints],
        }
        await self._redis.set(f"reflection:review:{signal.signal_id}", str(payload))
        if self._pg_pool is not None:
            await self._write_review_row(payload)
        if self._queue_adapter is not None:
            await self._queue_adapter.write(payload)

    async def _write_review_row(self, payload: dict[str, Any]) -> None:
        assert self._pg_pool is not None
        sql = (
            "/* reflection_log: DEQ checkpoints per signal */\n"
            "/* human_review_queue: WBFT divergence escalations */\n"
            "INSERT INTO human_review_queue (signal_id, reason, payload) "
            "VALUES ($1, $2, $3::jsonb)"
        )
        async with self._pg_pool.acquire() as conn:
            await conn.execute(sql, payload["signal_id"], payload["reason"], str(payload))

    def _apply_reflection_to_signal(
        self,
        signal: SignalOutput,
        result: ReflectionResult,
    ) -> SignalOutput:
        if self._settings.reflection.shadow_mode:
            return signal
        modifier = min(signal.position_size_modifier, result.selected_score / max(signal.score, 1))
        return signal.model_copy(update={"position_size_modifier": max(0.0, modifier)})


def _weighted_score(weights: list[WBFTAgentWeight]) -> float:
    total = sum(w.weight for w in weights) or 1.0
    return max(0.0, min(100.0, total * 100.0))


def _round_wbft_score(weights: list[WBFTAgentWeight], adjustment: int) -> float:
    baseline = _weighted_score(weights)
    return max(0.0, min(100.0, baseline - abs(adjustment)))


def _clamp_adjustment(adjustment: int, max_reduction: int) -> int:
    lower = -abs(max_reduction)
    return max(lower, min(0, adjustment))


def _clamp_score(score: int, score_floor: int) -> int:
    return max(score_floor, min(100, score))

