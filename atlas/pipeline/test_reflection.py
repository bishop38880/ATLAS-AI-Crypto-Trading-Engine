"""Tests for ReflectionCritic DEQ + WBFT flow."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from decimal import Decimal

import pytest

from atlas.core.llm_client import BaseLLMClient, LLMResponse
from atlas.models.signal import (
    CategoryScores,
    SignalDecision,
    SignalOutput,
)
from atlas.models.telemetry import TelemetryEvent
from atlas.pipeline.reflection import ReflectionCritic, WBFTWeighter
from atlas.shared.config import PolarisSettings, ReflectionConfig


class FakeRedis:
    """Simple async Redis double."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str) -> None:
        self.values[key] = value


class FakeLLM(BaseLLMClient):
    """Controllable LLM client for tests."""

    def __init__(self, response: str | list[str], delay_s: float = 0.0) -> None:
        self._response = response
        self._delay_s = delay_s
        self._idx = 0

    async def complete(self, prompt: str, max_tokens: int = 1000) -> LLMResponse:
        _ = prompt, max_tokens
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        text = self._next_text()
        return LLMResponse(
            text=text,
            reasoning=None,
            model="deepseek-chat",
            latency_ms=1,
            tokens_used=1,
            cost_estimate_usd=0.0,
        )

    async def health_check(self) -> bool:
        return True

    def _next_text(self) -> str:
        if isinstance(self._response, str):
            return self._response
        if self._idx >= len(self._response):
            return self._response[-1]
        text = self._response[self._idx]
        self._idx += 1
        return text


def _settings(**overrides: Any) -> PolarisSettings:
    reflection = ReflectionConfig(enabled=True, shadow_mode=False, **overrides)
    return PolarisSettings(reflection=reflection)


def _signal(raw: int = 170, score: int = 80) -> SignalOutput:
    now = datetime.now(timezone.utc)
    return SignalOutput(
        decision=SignalDecision.HOLD,
        asset="BTCUSDT",
        timeframe="30m",
        expires_at=now + timedelta(minutes=30),
        score=score,
        confidence=Decimal("0.9"),
        category_scores=CategoryScores(total=0),
        telemetry=TelemetryEvent(cycle_id="c1", cycle_latency_ms=1.0, agent_count=5),
        raw_confluence_score=raw,
        reasoning_summary="primary synthesis",
        key_risks=["risk_a"],
        agent_breakdown={},
        position_size_modifier=Decimal("1.0"),
    )


@pytest.mark.asyncio
async def test_disabled_path_skips_reflection() -> None:
    cfg = PolarisSettings(reflection=ReflectionConfig(enabled=False))
    critic = ReflectionCritic(cfg, FakeLLM("CONFIDENCE_ADJUSTMENT=-5"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(), {})
    assert env.result.skipped is True


@pytest.mark.asyncio
async def test_below_threshold_skips_reflection() -> None:
    critic = ReflectionCritic(_settings(score_threshold=200), FakeLLM("CONFIDENCE_ADJUSTMENT=-5"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(raw=150), {})
    assert env.result.skipped is True


@pytest.mark.asyncio
async def test_timeout_returns_round_zero() -> None:
    critic = ReflectionCritic(_settings(deq_timeout_s=0.01), FakeLLM("x", delay_s=0.05), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(), {})
    assert env.result.timed_out is True
    assert env.result.selected_round == 0


@pytest.mark.asyncio
async def test_convergence_stops_after_two_rounds() -> None:
    critic = ReflectionCritic(_settings(convergence_pts=11), FakeLLM("CONFIDENCE_ADJUSTMENT=-2"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(score=80), {})
    assert len(env.result.checkpoints) == 2


@pytest.mark.asyncio
async def test_divergence_flags_human_review() -> None:
    redis_client = FakeRedis()
    llm = FakeLLM([
        "CONFIDENCE_ADJUSTMENT=-1",
        "CONFIDENCE_ADJUSTMENT=-15",
    ])
    critic = ReflectionCritic(_settings(wbft_divergence_pts=1), llm, redis_client)  # type: ignore[arg-type]
    env = await critic.reflect(_signal(score=90), {})
    assert env.result.should_human_review is True


@pytest.mark.asyncio
async def test_max_rounds_respected() -> None:
    critic = ReflectionCritic(
        _settings(deq_max_rounds=2, convergence_pts=0),
        FakeLLM("CONFIDENCE_ADJUSTMENT=-15"),
        FakeRedis(),  # type: ignore[arg-type]
    )
    env = await critic.reflect(_signal(), {})
    assert len(env.result.checkpoints) == 2


@pytest.mark.asyncio
async def test_ceiling_and_floor_clamp() -> None:
    critic = ReflectionCritic(_settings(min_score_floor=55), FakeLLM("CONFIDENCE_ADJUSTMENT=-99"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(score=60), {})
    assert env.result.selected_score >= 55


@pytest.mark.asyncio
async def test_parsing_missing_marker_defaults_zero() -> None:
    critic = ReflectionCritic(_settings(), FakeLLM("noise"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(score=77), {})
    assert env.result.selected_score == 77


@pytest.mark.asyncio
async def test_weight_normalization_sums_to_one() -> None:
    weighter = WBFTWeighter(FakeRedis({"wbft:agent:a1:quality": "1", "wbft:agent:a2:quality": "1"}))  # type: ignore[arg-type]
    weights = await weighter.compute(["a1", "a2"])
    total = sum(w.weight for w in weights)
    assert round(total, 6) == 1.0


@pytest.mark.asyncio
async def test_redis_miss_defaults() -> None:
    weighter = WBFTWeighter(FakeRedis())  # type: ignore[arg-type]
    weights = await weighter.compute(["x"])
    assert weights[0].quality == 0.8
    assert weights[0].sharpe == 0.0


@pytest.mark.asyncio
async def test_drawdown_skip() -> None:
    critic = ReflectionCritic(_settings(), FakeLLM("CONFIDENCE_ADJUSTMENT=-5"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(_signal(), {"portfolio_drawdown_4h_pct": 6.0})
    assert env.result.skipped is True


@pytest.mark.asyncio
async def test_shadow_mode_noop_behavior() -> None:
    cfg = PolarisSettings(reflection=ReflectionConfig(enabled=True, shadow_mode=True))
    sig = _signal(score=80)
    critic = ReflectionCritic(cfg, FakeLLM("CONFIDENCE_ADJUSTMENT=-10"), FakeRedis())  # type: ignore[arg-type]
    env = await critic.reflect(sig, {})
    assert env.signal.position_size_modifier == sig.position_size_modifier
