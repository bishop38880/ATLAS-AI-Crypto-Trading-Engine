"""Tests for RLVR trainer."""

import pytest
import torch

from atlas.ml.rlvr.state_encoder import STATE_DIM
from atlas.ml.rlvr.trainer import RLVRTrainer


def test_warm_start_bullish() -> None:
    """Verify decision head initialization for bullish meta-learner."""
    trainer = RLVRTrainer(meta_learner_bullish=True, thompson_entropy=0.2)
    bias = trainer.policy.decision_head.bias.detach()
    assert bias[0].item() > bias[1].item()  # LONG > SHORT


def test_warm_start_bearish() -> None:
    """Verify decision head initialization for bearish meta-learner."""
    trainer = RLVRTrainer(meta_learner_bullish=False, thompson_entropy=0.2)
    bias = trainer.policy.decision_head.bias.detach()
    assert bias[1].item() > bias[0].item()  # SHORT > LONG


def test_warm_start_neutral() -> None:
    """Verify decision head initialization for neutral meta-learner."""
    trainer = RLVRTrainer(meta_learner_bullish=None, thompson_entropy=0.2)
    bias = trainer.policy.decision_head.bias.detach()
    assert bias[0].item() == 0.0
    assert bias[1].item() == 0.0


def test_warm_start_confidence() -> None:
    """Verify confidence initialization from Thompson entropy."""
    trainer_high_ent = RLVRTrainer(meta_learner_bullish=None, thompson_entropy=0.9)
    trainer_low_ent = RLVRTrainer(meta_learner_bullish=None, thompson_entropy=0.1)

    bias_high = trainer_high_ent.policy.confidence_head[0].bias.detach().item()  # type: ignore[union-attr]
    bias_low = trainer_low_ent.policy.confidence_head[0].bias.detach().item()  # type: ignore[union-attr]

    assert bias_high < bias_low  # Higher entropy -> lower initial confidence bias


@pytest.mark.asyncio
async def test_train_step_async() -> None:
    """Verify async training step."""
    trainer = RLVRTrainer(meta_learner_bullish=None, thompson_entropy=0.5)
    
    # Mock buffer
    buffer = {
        "states": torch.randn(4, STATE_DIM),
        "actions": torch.randn(4, 3),  # Logits placeholder
        "advantages": torch.randn(4),
        "log_probs": torch.randn(4),
    }

    loss = await trainer.train_step_async(buffer)
    assert isinstance(loss, float)
