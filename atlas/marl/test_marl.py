"""Tests for MARL cooperative deliberation shadow package."""

from __future__ import annotations

from typing import Any

import pytest
import torch

from atlas.agents.base import SignalDirection
from atlas.marl.deliberation import DeliberationCoordinator
from atlas.marl.mappo_trainer import MAPPOTrainer
from atlas.marl.message_encoder import AgentMessage, MessageEncoder
from atlas.marl.revision_head import RevisionHead, RevisionHeadConfig
from atlas.marl.shadow_injector import ShadowInjector


class _FakeRedis:
    def __init__(self, mapping: dict[str, bytes | str | None]) -> None:
        self._mapping = mapping

    async def get(self, key: str) -> bytes | str | None:
        return self._mapping.get(key)

    async def mget(self, *keys: str) -> list[bytes | str | None]:
        return [self._mapping.get(key) for key in keys]


class _RaisingInjector:
    async def fetch(self, asset: str) -> Any:
        raise RuntimeError(f"boom-{asset}")


class _FixedRevisionHead:
    def __init__(self, value: float) -> None:
        self._value = value

    @property
    def input_dim(self) -> int:
        return 24

    def revise(self, features: list[float]) -> float:
        _ = features
        return self._value


def _message(direction: SignalDirection = SignalDirection.BULLISH) -> AgentMessage:
    return AgentMessage(
        agent_name="technical",
        score=110,
        max_score=220,
        direction=direction,
        risk_veto=False,
    )


def test_encoder_infers_direction() -> None:
    encoder = MessageEncoder()
    assert encoder._infer_direction(SignalDirection.BULLISH) == 1.0
    assert encoder._infer_direction(SignalDirection.BEARISH) == -1.0
    assert encoder._infer_direction(SignalDirection.NEUTRAL) == 0.0


@pytest.mark.asyncio
async def test_encode_vector_length_and_content() -> None:
    encoder = MessageEncoder()
    injector = ShadowInjector(_FakeRedis({}))
    metrics = await injector.fetch("BTCUSDT")
    vector = encoder.encode(_message(), metrics)
    assert len(vector) == 5
    assert vector[0] == 0.5
    assert vector[2] == 1.0


@pytest.mark.asyncio
async def test_shadow_injector_defaults_on_missing_values() -> None:
    injector = ShadowInjector(_FakeRedis({}))
    metrics = await injector.fetch("ETHUSDT")
    assert metrics.avg_pairwise_correlation == 0.0
    assert metrics.volatility_zscore == 0.0


@pytest.mark.asyncio
async def test_shadow_injector_parses_values() -> None:
    base = "atlas:marl:shadow:BTCUSDT:"
    injector = ShadowInjector(
        _FakeRedis(
            {
                f"{base}avg_pairwise_correlation": b"0.74",
                f"{base}volatility_zscore": "1.25",
            },
        ),
    )
    metrics = await injector.fetch("BTCUSDT")
    assert metrics.avg_pairwise_correlation == 0.74
    assert metrics.volatility_zscore == 1.25


def test_revision_head_returns_zero_without_checkpoint() -> None:
    head = RevisionHead(RevisionHeadConfig(checkpoint_path=""))
    value = head.revise([0.1] * 24)
    assert value == 0.0


def test_revision_head_forward_clamps() -> None:
    config = RevisionHeadConfig(max_revision=0.15)
    head = RevisionHead(config)
    x = torch.ones((1, 24), dtype=torch.float32)
    out = head.forward(x).item()
    assert -0.15 <= out <= 0.15


@pytest.mark.asyncio
async def test_deliberation_returns_round1_on_veto() -> None:
    coordinator = DeliberationCoordinator(
        injector=ShadowInjector(_FakeRedis({})),
        encoder=MessageEncoder(),
        revision_head=_FixedRevisionHead(0.12),
    )
    value = await coordinator.deliberate(57.0, True, _message(), "BTCUSDT")
    assert value == 57.0


@pytest.mark.asyncio
async def test_deliberation_applies_revision_without_stress() -> None:
    base = "atlas:marl:shadow:BTCUSDT:"
    coordinator = DeliberationCoordinator(
        injector=ShadowInjector(
            _FakeRedis({f"{base}avg_pairwise_correlation": "0.60"}),
        ),
        encoder=MessageEncoder(),
        revision_head=_FixedRevisionHead(0.12),
    )
    value = await coordinator.deliberate(50.0, False, _message(), "BTCUSDT")
    assert value == pytest.approx(56.0)


@pytest.mark.asyncio
async def test_deliberation_caps_revision_under_correlation_stress() -> None:
    base = "atlas:marl:shadow:BTCUSDT:"
    coordinator = DeliberationCoordinator(
        injector=ShadowInjector(
            _FakeRedis({f"{base}avg_pairwise_correlation": "0.91"}),
        ),
        encoder=MessageEncoder(),
        revision_head=_FixedRevisionHead(0.12),
    )
    value = await coordinator.deliberate(80.0, False, _message(), "BTCUSDT")
    assert value == pytest.approx(84.0)


@pytest.mark.asyncio
async def test_deliberation_handles_exception_safely() -> None:
    coordinator = DeliberationCoordinator(
        injector=_RaisingInjector(),
        encoder=MessageEncoder(),
        revision_head=_FixedRevisionHead(0.12),
    )
    value = await coordinator.deliberate(63.0, False, _message(), "BTCUSDT")
    assert value == 63.0


@pytest.mark.asyncio
async def test_encode_all_returns_parallel_vectors() -> None:
    encoder = MessageEncoder()
    injector = ShadowInjector(_FakeRedis({}))
    metrics = await injector.fetch("BTCUSDT")
    vectors = encoder.encode_all([_message(), _message(SignalDirection.BEARISH)], metrics)
    assert len(vectors) == 2
    assert vectors[0][2] == 1.0
    assert vectors[1][2] == -1.0


@pytest.mark.asyncio
async def test_mappo_trainer_stub_raises() -> None:
    trainer = MAPPOTrainer()
    with pytest.raises(NotImplementedError):
        await trainer.train_offline()
