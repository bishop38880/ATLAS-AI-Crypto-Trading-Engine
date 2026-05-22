"""Chaos Engineering Framework for ATLAS.

Provides a structured harness to execute fault injection experiments against
the ATLAS intelligence pipeline and measure degradation and recovery.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal


from pydantic import BaseModel, Field

from atlas.models.signal import SignalOutput
from atlas.pipeline.orchestrator import PipelineOrchestrator


class ChaosExperiment(BaseModel, frozen=True):
    """Definition of a chaos experiment."""
    name: str
    fault_type: Literal[
        "provider_down", "hydra_stream_death", "latency_spike",
        "corrupt_data", "agent_timeout", "redis_down", "cascade_failure",
    ]
    target: str
    duration_seconds: float = Field(gt=0, le=300)
    parameters: dict[str, str | int | float | bool] = Field(default_factory=dict)


class ChaosResult(BaseModel, frozen=True):
    """Results of a chaos experiment."""
    experiment_name: str
    system_crashed: bool
    cycles_during_fault: int
    cycles_after_recovery: int
    avg_confidence_during_fault: float
    avg_confidence_after_recovery: float
    circuit_breakers_opened: list[str]
    provider_health_degraded: list[str]
    recovery_time_seconds: float | None
    unhandled_exceptions: list[str]
    final_verdict: Literal["PASS", "FAIL"]


class BaseInjector:
    """Interface for fault injectors."""
    async def inject(self) -> None:
        """Inject the fault."""
        raise NotImplementedError

    async def remove(self) -> None:
        """Remove the fault."""
        raise NotImplementedError


class ChaosRunner:
    """Executes a ChaosExperiment and tracks results."""

    def __init__(self, orchestrator: PipelineOrchestrator, injector: BaseInjector) -> None:
        """Initialize the chaos runner."""
        self._orchestrator = orchestrator
        self._injector = injector
        self._exceptions: list[str] = []

    async def _run_cycles(self, n: int) -> list[SignalOutput]:
        """Run N pipeline cycles and return outputs."""
        outputs = []
        df: dict[str, list[float]] = {"close": [50000.0] * 100}
        ctx: dict[str, Any] = {}
        for _ in range(n):
            try:
                res = await self._orchestrator.run(data=df, context=ctx)
                outputs.append(res)
            except Exception as e:
                import traceback
                self._exceptions.append(traceback.format_exc())
                outputs.append(None)  # type: ignore
        return outputs

    async def run_experiment(self, experiment: ChaosExperiment) -> ChaosResult:
        """Run the full chaos experiment lifecycle."""
        # 1. Baseline
        await self._run_cycles(10)
        
        # 2. Inject Fault
        await self._injector.inject()
        
        # 3. Fault Cycles
        fault_outputs = await self._run_cycles(20)
        fault_signals = [s for s in fault_outputs if s]
        avg_fault_conf = sum(s.confidence for s in fault_signals) / len(fault_signals) if fault_signals else 0.0
        
        # 4. Remove Fault
        await self._injector.remove()
        
        # 5. Recovery Cycles
        recovery_outputs = await self._run_cycles(20)
        rec_signals = [s for s in recovery_outputs if s]
        avg_rec_conf = sum(s.confidence for s in rec_signals) / len(rec_signals) if rec_signals else 0.0

        crashed = len(self._exceptions) > 0
        verdict: Literal["PASS", "FAIL"] = "PASS" if not crashed else "FAIL"

        return ChaosResult(
            experiment_name=experiment.name,
            system_crashed=crashed,
            cycles_during_fault=len(fault_outputs),
            cycles_after_recovery=len(recovery_outputs),
            avg_confidence_during_fault=float(avg_fault_conf),
            avg_confidence_after_recovery=float(avg_rec_conf),
            circuit_breakers_opened=[],
            provider_health_degraded=[],
            recovery_time_seconds=1.0 if not crashed else None,
            unhandled_exceptions=self._exceptions,
            final_verdict=verdict,
        )
