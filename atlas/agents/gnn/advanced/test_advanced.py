"""Tests for advanced GNN features (H3 Shadow Mode)."""

import subprocess
import time
from unittest.mock import AsyncMock, patch

import msgspec
import pytest
import torch

from atlas.agents.gnn.advanced.gog_layer import GoGMetaGraph
from atlas.agents.gnn.advanced.stress_trigger import (
    StressTriggerEvent,
    publish_stress_trigger,
)
from atlas.agents.gnn.advanced.wavelet_encoder import extract_wavelet_features


def test_gog_layer_forward():
    """1. GoG layer forward: two 32-dim embeddings -> scalar contagion in [0, 1]."""
    layer = GoGMetaGraph(embed_dim=32)
    layer.eval()

    graph_a = torch.randn(4, 32)
    graph_b = torch.randn(4, 32)

    with torch.no_grad():
        out = layer(graph_a, graph_b)

    assert out.shape == (4, 1)
    assert (out >= 0.0).all() and (out <= 1.0).all()


def test_gog_layer_param_count():
    """2. GoG layer param count <= 5K."""
    layer = GoGMetaGraph(embed_dim=32)
    total_params = sum(p.numel() for p in layer.parameters() if p.requires_grad)
    assert total_params <= 5000
    assert total_params > 0


def test_wavelet_encoder_dimensions():
    """3. Wavelet encoder: 128-length input -> 48-dim feature vector."""
    ts = [0.0] * 128
    out = extract_wavelet_features(ts)
    assert out.shape == (48,)
    assert out.dtype == torch.float32


@patch("atlas.agents.gnn.advanced.wavelet_encoder.HAS_PYWT", False)
@patch("atlas.agents.gnn.advanced.wavelet_encoder.logger.warning")
def test_wavelet_encoder_fallback(mock_warning):
    """4. Wavelet encoder fallback: pywt import failure -> zero tensor, warning logged."""
    ts = [1.0] * 128
    out = extract_wavelet_features(ts)

    assert out.shape == (48,)
    assert (out == 0.0).all()
    mock_warning.assert_called_once_with("pywavelets unavailable; wavelet features zeroed")


def test_wavelet_encoder_benchmark():
    """5. Wavelet encoder benchmark: 32-node batch in < 5ms (CPU)."""
    ts_batch = [[float(i)] * 128 for i in range(32)]

    start = time.perf_counter()
    for ts in ts_batch:
        extract_wavelet_features(ts)
    end = time.perf_counter()

    elapsed_ms = (end - start) * 1000
    assert elapsed_ms < 5.0, f"Wavelet extraction took {elapsed_ms:.2f}ms, > 5ms limit"


@pytest.mark.asyncio
async def test_stress_trigger_exceeds_threshold():
    """6. Stress trigger: contagion > 0.75 -> Redis SET called with TTL 1800."""
    graph_a = torch.randn(32)
    graph_b = torch.randn(32)

    mock_redis = AsyncMock()

    await publish_stress_trigger(
        asset="BTC",
        contagion_signal=0.80,
        threshold=0.75,
        graph_a_emb=graph_a,
        graph_b_emb=graph_b,
        redis_client=mock_redis,
    )

    mock_redis.set.assert_called_once()
    args, kwargs = mock_redis.set.call_args
    assert kwargs.get("ex") == 1800
    assert args[0] == "atlas:gnn:stress_trigger:BTC"


@pytest.mark.asyncio
async def test_stress_trigger_below_threshold():
    """7. Stress trigger: contagion <= 0.75 -> no Redis write."""
    graph_a = torch.randn(32)
    graph_b = torch.randn(32)

    mock_redis = AsyncMock()

    await publish_stress_trigger(
        asset="BTC",
        contagion_signal=0.50,
        threshold=0.75,
        graph_a_emb=graph_a,
        graph_b_emb=graph_b,
        redis_client=mock_redis,
    )

    mock_redis.set.assert_not_called()


def test_stress_trigger_payload_round_trip():
    """8. Stress trigger payload round-trips via msgspec.json.encode/decode."""
    event = StressTriggerEvent(
        asset="ETH",
        contagion_signal=0.90,
        threshold_crossed=0.75,
        computed_at="2026-04-25T00:00:00Z",
        graph_embeddings_hash="dummy_hash",
    )

    encoded = msgspec.json.encode(event)
    decoded = msgspec.json.decode(encoded, type=StressTriggerEvent)

    assert decoded.asset == "ETH"
    assert decoded.contagion_signal == 0.90
    assert decoded.graph_embeddings_hash == "dummy_hash"


def test_no_live_score_leakage():
    """9. grep -rn 'confluence_score|live_score|add_to_score' atlas/agents/gnn/advanced/ returns zero."""
    import pathlib
    advanced_dir = pathlib.Path(__file__).parent
    for f in advanced_dir.rglob("*.py"):
        if f.name == "test_advanced.py":
            continue
        content = f.read_text()
        assert "confluence_score" not in content
        assert "live_score" not in content
        assert "add_to_score" not in content


def test_no_banned_imports():
    """10. Check for aioredis, standard json imports, etc. in advanced module."""
    import pathlib
    advanced_dir = pathlib.Path(__file__).parent
    for f in advanced_dir.rglob("*.py"):
        if f.name == "test_advanced.py":
            continue
        content = f.read_text()
        assert "import aioredis" not in content
        assert "from aioredis" not in content
        assert "import json" not in content
        assert "from json import" not in content
