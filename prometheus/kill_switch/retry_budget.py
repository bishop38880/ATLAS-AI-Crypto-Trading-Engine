"""Retry budget — tracks cumulative retry time and enforces escalation.

Budget semantics:
    - ``escalation_threshold`` (60 s): log critical + notify operator.
    - ``total_budget`` (120 s): trigger kill switch HALT automatically.

The old 600-second budget was too permissive — 10 minutes of retry storms
can accumulate severe losses.  2 minutes is the ceiling.
"""

import time
from dataclasses import dataclass, field


@dataclass
class RetryBudget:
    """Tracks elapsed retry time with escalation and exhaustion thresholds.

    Attributes:
        total_budget_seconds: Maximum retry duration before automatic halt.
        escalation_threshold_seconds: Duration before critical escalation.
    """

    total_budget_seconds: float = 120.0
    escalation_threshold_seconds: float = 60.0
    _elapsed: float = 0.0
    _last_check: float = field(default_factory=time.monotonic)

    def tick(self) -> float:
        """Advance the budget clock and return total elapsed seconds."""
        now = time.monotonic()
        self._elapsed += now - self._last_check
        self._last_check = now
        return self._elapsed

    def should_escalate(self) -> bool:
        """Return ``True`` if elapsed time has reached the escalation threshold."""
        return self._elapsed >= self.escalation_threshold_seconds

    def budget_exhausted(self) -> bool:
        """Return ``True`` if the total retry budget has been exhausted."""
        return self._elapsed >= self.total_budget_seconds
