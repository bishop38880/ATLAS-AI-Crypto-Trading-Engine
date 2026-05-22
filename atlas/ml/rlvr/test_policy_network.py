"""Tests for RLVR policy network."""

import pytest
import torch

from atlas.ml.rlvr.policy_network import SignalPolicyNetwork
from atlas.ml.rlvr.state_encoder import STATE_DIM


@pytest.mark.asyncio
async def test_policy_network_forward_async() -> None:
    """Verify async forward pass returns correctly shaped tensors."""
    net = SignalPolicyNetwork()
    x = torch.randn(1, STATE_DIM)

    logits, confidence, weights = await net.forward_async(x)

    assert logits.shape == (1, 3)
    assert confidence.shape == (1, 1)
    assert weights.shape == (1, 5)

    assert (confidence >= 0.0).all() and (confidence <= 1.0).all()


def test_policy_network_latency() -> None:
    """Verify CPU forward pass latency is acceptable."""
    net = SignalPolicyNetwork()
    x = torch.randn(1, STATE_DIM)

    # Warmup
    _ = net(x)

    import time
    start = time.perf_counter()
    _ = net(x)
    elapsed = (time.perf_counter() - start) * 1000

    assert elapsed < 5.0  # Must be under 5ms
