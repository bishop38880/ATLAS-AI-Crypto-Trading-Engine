"""CPU-only revision head used in MARL shadow mode."""

from __future__ import annotations

from pathlib import Path

import torch
from pydantic import BaseModel, ConfigDict, Field


class RevisionHeadConfig(BaseModel):
    """Frozen config for revision head architecture and bounds."""

    model_config = ConfigDict(frozen=True)

    input_dim: int = Field(default=24, ge=1)
    hidden: int = Field(default=32, ge=1)
    output: int = Field(default=1, ge=1)
    max_revision: float = Field(default=0.15, gt=0.0, le=1.0)
    checkpoint_path: str = ""


class RevisionHead(torch.nn.Module):
    """Small MLP that predicts a bounded revision factor."""

    def __init__(self, config: RevisionHeadConfig | None = None) -> None:
        super().__init__()
        self.config = config or RevisionHeadConfig()
        self._network = torch.nn.Sequential(
            torch.nn.Linear(self.config.input_dim, self.config.hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(self.config.hidden, self.config.hidden),
            torch.nn.ReLU(),
            torch.nn.Linear(self.config.hidden, self.config.output),
            torch.nn.Tanh(),
        )
        self._checkpoint_loaded = self._load_checkpoint_if_present()

    def _load_checkpoint_if_present(self) -> bool:
        path = self.config.checkpoint_path.strip()
        if not path:
            return False
        target = Path(path)
        if not target.exists():
            return False
        state_dict = torch.load(target, map_location="cpu")
        self._network.load_state_dict(state_dict)
        self.eval()
        return True

    @property
    def input_dim(self) -> int:
        return self.config.input_dim

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        max_revision = self.config.max_revision
        with torch.no_grad():
            output = self._network(features)
            bounded = output * max_revision
            return torch.clamp(bounded, -max_revision, max_revision)

    def revise(self, features: list[float]) -> float:
        if not self._checkpoint_loaded:
            return 0.0
        x = torch.tensor(features, dtype=torch.float32).reshape(1, -1)
        value = float(self.forward(x).item())
        max_revision = self.config.max_revision
        return max(-max_revision, min(max_revision, value))
