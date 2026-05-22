"""Action decoder for RLVR."""

from __future__ import annotations

from typing import Literal

import numpy as np
from pydantic import BaseModel, Field


def project_weight_adjustments(raw: np.ndarray, clip: float = 0.1) -> np.ndarray:
    """Project raw policy output to satisfy (a) |a_i| <= clip, (b) sum(a) == 0.

    Algorithm:
    1. Clip to [-clip, +clip].
    2. Subtract mean to enforce zero-sum.
    3. Re-clip and iteratively redistribute residual until converged.
    """
    a = np.clip(raw, -clip, clip)
    a = a - a.mean()
    a = np.clip(a, -clip, clip)
    residual = a.sum()
    if abs(residual) > 1e-9:
        idx = int(np.argmin(np.abs(a)))
        a[idx] -= residual
        a = np.clip(a, -clip, clip)
    return a


class RLVRAction(BaseModel, frozen=True):
    """Immutable action from RLVR policy network."""

    trade_decision: Literal["LONG", "SHORT", "ABSTAIN"]
    trade_confidence: float = Field(ge=0.0, le=1.0)
    weight_adjustments: list[float] = Field(min_length=5, max_length=5)
