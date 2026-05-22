"""MARL cooperative deliberation package (shadow mode only)."""

from atlas.marl.deliberation import DeliberationCoordinator
from atlas.marl.mappo_trainer import MAPPOTrainer
from atlas.marl.message_encoder import AgentMessage, MessageEncoder, ShadowMetrics
from atlas.marl.revision_head import RevisionHead, RevisionHeadConfig
from atlas.marl.shadow_injector import ShadowInjector

__all__ = [
    "AgentMessage",
    "DeliberationCoordinator",
    "MAPPOTrainer",
    "MessageEncoder",
    "RevisionHead",
    "RevisionHeadConfig",
    "ShadowInjector",
    "ShadowMetrics",
]
