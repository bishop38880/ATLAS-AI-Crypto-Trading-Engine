"""Pre-built Chaos Experiments."""

from atlas.testing.chaos.framework import ChaosExperiment
from atlas.testing.chaos.injectors import (
    ProviderDownInjector,
    HydraStreamDeathInjector,
    LatencySpikeInjector,
    CorruptDataInjector,
    AgentTimeoutInjector,
    RedisDownInjector,
    MultiInjector,
    BaseInjector,
)

EXPERIMENTS = {
    "single_provider_down": ChaosExperiment(
        name="single_provider_down",
        fault_type="provider_down",
        target="coinalyze",
        duration_seconds=10.0,
    ),
    "hydra_stream_disconnect": ChaosExperiment(
        name="hydra_stream_disconnect",
        fault_type="hydra_stream_death",
        target="hydra",
        duration_seconds=10.0,
    ),
    "latency_spike_defillama": ChaosExperiment(
        name="latency_spike_defillama",
        fault_type="latency_spike",
        target="defillama",
        duration_seconds=10.0,
        parameters={"delay_s": 0.5},
    ),
    "corrupt_hydra_matrix": ChaosExperiment(
        name="corrupt_hydra_matrix",
        fault_type="corrupt_data",
        target="hydra",
        duration_seconds=10.0,
    ),
    "tier1_analysts_timeout_2_of_5": ChaosExperiment(
        name="tier1_analysts_timeout_2_of_5",
        fault_type="agent_timeout",
        target="multiple",
        duration_seconds=10.0,
    ),
    "tier1_analysts_timeout_3_of_5": ChaosExperiment(
        name="tier1_analysts_timeout_3_of_5",
        fault_type="agent_timeout",
        target="multiple",
        duration_seconds=10.0,
    ),
    "tier2_risk_timeout": ChaosExperiment(
        name="tier2_risk_timeout",
        fault_type="agent_timeout",
        target="risk_agent",
        duration_seconds=10.0,
    ),
    "redis_flap": ChaosExperiment(
        name="redis_flap",
        fault_type="redis_down",
        target="redis",
        duration_seconds=5.0,
    ),
    "cascade_failure": ChaosExperiment(
        name="cascade_failure",
        fault_type="cascade_failure",
        target="multiple",
        duration_seconds=15.0,
    ),
}

def get_injector_for_experiment(name: str) -> BaseInjector:
    """Get the injector associated with an experiment."""
    if name == "single_provider_down":
        return ProviderDownInjector("coinalyze")
    elif name == "hydra_stream_disconnect":
        return HydraStreamDeathInjector()
    elif name == "latency_spike_defillama":
        return LatencySpikeInjector("defillama", 0.5)
    elif name == "corrupt_hydra_matrix":
        return CorruptDataInjector("hydra")
    elif name == "tier1_analysts_timeout_2_of_5":
        return MultiInjector([
            AgentTimeoutInjector("technical_agent"),
            AgentTimeoutInjector("derivatives_agent")
        ])
    elif name == "tier1_analysts_timeout_3_of_5":
        return MultiInjector([
            AgentTimeoutInjector("technical_agent"),
            AgentTimeoutInjector("derivatives_agent"),
            AgentTimeoutInjector("sentiment_agent")
        ])
    elif name == "tier2_risk_timeout":
        return AgentTimeoutInjector("risk_agent")
    elif name == "redis_flap":
        return RedisDownInjector()
    elif name == "cascade_failure":
        # Note: True cascade would offset the second failure by 5s. We can use LatencySpike and Down.
        # But for test harness, injecting both sequentially during inject is fine for now.
        return MultiInjector([
            ProviderDownInjector("coinalyze"),
            ProviderDownInjector("defillama"),
        ])
    raise ValueError(f"Unknown experiment: {name}")
