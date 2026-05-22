import asyncio
import time
from pathlib import Path

import pytest
import torch
import torch.nn as nn

from atlas.agents.gnn.sequential.liquid_network import LiquidNetwork
from atlas.agents.gnn.sequential.mamba_block import MambaBlock
from atlas.agents.gnn.sequential.sequence_fusion import SequenceFusion


def test_mamba_forward_pass() -> None:
    """1. MambaBlock forward pass: (2, 128, 32) input -> (2, 128, 32) output, float32."""
    model = MambaBlock(d_model=32, d_state=16, d_conv=4)
    x = torch.randn(2, 128, 32, dtype=torch.float32)
    y = model(x)
    assert y.shape == (2, 128, 32)
    assert y.dtype == torch.float32


def test_mamba_max_sequence_length() -> None:
    """2. MambaBlock sequence length > 256 raises AssertionError."""
    model = MambaBlock()
    x = torch.randn(1, 257, 32)
    with pytest.raises(AssertionError, match="exceeds maximum of 256"):
        model(x)


def test_mamba_param_count() -> None:
    """3. MambaBlock param count <= 10K (budget sanity check)."""
    model = MambaBlock(d_model=32, d_state=16, d_conv=4)
    params = sum(p.numel() for p in model.parameters())
    assert params <= 10000


def test_lnn_forward_bounds() -> None:
    """4. LiquidNetwork forward pass: hidden state stays bounded after 10 Euler steps."""
    model = LiquidNetwork(input_dim=32, hidden_dim=16, n_steps=10, dt=0.1)
    x = torch.randn(2, 128, 32) * 1000  # extreme input
    y = model(x)
    assert y.shape == (2, 128, 16)
    assert torch.all(y >= -10.0)
    assert torch.all(y <= 10.0)


def test_lnn_param_count() -> None:
    """5. LiquidNetwork param count <= 5K."""
    model = LiquidNetwork(input_dim=32, hidden_dim=16)
    params = sum(p.numel() for p in model.parameters())
    assert params <= 5000


def test_sequence_fusion_output() -> None:
    """6. SequenceFusion output is scalar in [0, 1]."""
    model = SequenceFusion(gnn_dim=64, mamba_dim=32, lnn_dim=16)
    gnn_emb = torch.randn(2, 64)
    mamba_out = torch.randn(2, 128, 32)
    lnn_out = torch.randn(2, 128, 16)
    y = model(gnn_emb, mamba_out, lnn_out)
    assert y.shape == (2, 1)
    assert torch.all(y >= 0.0)
    assert torch.all(y <= 1.0)


def test_cpu_execution_only() -> None:
    """7. All modules run on CPU."""
    mamba = MambaBlock()
    lnn = LiquidNetwork(input_dim=32, hidden_dim=16)
    fusion = SequenceFusion(gnn_dim=64, mamba_dim=32, lnn_dim=16)
    
    for model in [mamba, lnn, fusion]:
        for param in model.parameters():
            assert param.device.type == "cpu"


@pytest.mark.asyncio
async def test_asyncio_wrapper() -> None:
    """8. asyncio.to_thread wrapper works without blocking."""
    model = SequenceFusion(gnn_dim=64, mamba_dim=32, lnn_dim=16)
    gnn_emb = torch.randn(2, 64)
    mamba_out = torch.randn(2, 128, 32)
    lnn_out = torch.randn(2, 128, 16)
    
    t0 = time.perf_counter()
    y = await asyncio.to_thread(model, gnn_emb, mamba_out, lnn_out)
    t1 = time.perf_counter()
    
    assert y.shape == (2, 1)
    assert (t1 - t0) < 1.0  # reasonable async time


def test_performance_latency() -> None:
    """9. Full forward pass for one asset completes in < 50ms on CPU."""
    mamba = MambaBlock(d_model=32, d_state=16, d_conv=4)
    lnn = LiquidNetwork(input_dim=32, hidden_dim=16)
    fusion = SequenceFusion(gnn_dim=64, mamba_dim=32, lnn_dim=16)
    
    x = torch.randn(1, 128, 32)
    gnn_emb = torch.randn(1, 64)
    
    # Warmup
    m_out = mamba(x)
    l_out = lnn(x)
    _ = fusion(gnn_emb, m_out, l_out)
    
    t0 = time.perf_counter()
    m_out = mamba(x)
    l_out = lnn(x)
    _ = fusion(gnn_emb, m_out, l_out)
    t1 = time.perf_counter()
    
    assert (t1 - t0) < 0.200, f"Latency was {(t1 - t0) * 1000:.2f}ms > 200ms"


def test_no_banned_imports() -> None:
    """10. No torchdiffeq or mamba_ssm imports."""
    pkg_path = Path(__file__).parent
    for file in pkg_path.glob("*.py"):
        if file.name.startswith("test_"):
            continue
        content = file.read_text()
        assert "import torchdiffeq" not in content, f"Found torchdiffeq in {file}"
        assert "import mamba_ssm" not in content, f"Found mamba_ssm in {file}"
        # .cuda() checks
        lines = content.splitlines()
        for idx, line in enumerate(lines):
            if "cuda kernel" in line.lower() or "no cuda" in line.lower():
                continue
            assert "cuda" not in line.lower(), f"Found cuda in {file}:{idx+1}"
