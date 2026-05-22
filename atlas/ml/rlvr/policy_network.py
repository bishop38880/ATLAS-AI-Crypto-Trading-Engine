"""Signal Policy Network for RLVR."""

from __future__ import annotations

import asyncio
import time

import torch
import torch.nn as nn
from loguru import logger

from atlas.ml.rlvr.state_encoder import STATE_DIM


class SignalPolicyNetwork(nn.Module):
    """CPU-only MLP for RLVR state processing.

    Architecture: 42 -> 64 -> 32 -> action_dim
    """

    def __init__(self) -> None:
        super().__init__()
        
        # Backbone MLP
        self.backbone = nn.Sequential(
            nn.Linear(STATE_DIM, 64),
            nn.ReLU(),
            nn.LayerNorm(64),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.LayerNorm(32),
        )

        # Separate heads
        self.decision_head = nn.Linear(32, 3)     # logits for LONG, SHORT, ABSTAIN
        self.confidence_head = nn.Sequential(
            nn.Linear(32, 1),
            nn.Sigmoid()
        )
        self.weight_head = nn.Linear(32, 5)       # Raw adjustments for 5 agents

        # Initialization
        self._initialize_weights()

    def _initialize_weights(self) -> None:
        """Kaiming init for backbone."""
        for m in self.backbone.modules():
            if isinstance(m, nn.Linear):
                nn.init.kaiming_normal_(m.weight, mode='fan_in', nonlinearity='relu')
                if m.bias is not None:
                    nn.init.constant_(m.bias, 0)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Forward pass (< 5ms on CPU)."""
        start = time.perf_counter()
        
        features = self.backbone(x)
        logits = self.decision_head(features)
        confidence = self.confidence_head(features)
        weights = self.weight_head(features)
        
        elapsed = (time.perf_counter() - start) * 1000
        if elapsed > 5.0:
            logger.warning("policy_network_slow | elapsed_ms={:.2f}", elapsed)
            
        return logits, confidence, weights

    async def forward_async(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """Async wrapper to protect the UVLoop."""
        return await asyncio.to_thread(self.forward, x)
