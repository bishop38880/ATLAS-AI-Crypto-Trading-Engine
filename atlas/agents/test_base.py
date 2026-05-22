"""Tests for BaseAgent lifecycle — warming up, degraded, and recovery."""

import pytest
from atlas.agents.base import BaseAgent
from atlas.models.signal import AgentState, AgentCategory, AgentTier

class TestAgentState:
    """AgentState lifecycle (Audit S1.4)."""

    def test_warmup_transitions(self) -> None:
        """Agent transitions from WARMING_UP to READY."""
        class DummyAgent(BaseAgent):
            MIN_SAMPLES_TO_EMIT = 3
            @property
            def name(self) -> str: return "dummy"
            @property
            def category(self): return AgentCategory.CONTEXT
            @property
            def tier(self): return AgentTier.ANALYST
            async def score(self, data, context=None): return self._make_zero_result("test")

        agent = DummyAgent()
        assert agent.state == AgentState.WARMING_UP
        assert not agent.is_ready

        agent.record_sample()
        agent.record_sample()
        assert agent.state == AgentState.WARMING_UP

        agent.record_sample()
        assert agent.state == AgentState.READY
        assert agent.is_ready

    def test_degraded_and_recovery(self) -> None:
        """Agent degrades and recovers."""
        class DummyAgent2(BaseAgent):
            MIN_SAMPLES_TO_EMIT = 1
            @property
            def name(self) -> str: return "dummy2"
            @property
            def category(self): return AgentCategory.CONTEXT
            @property
            def tier(self): return AgentTier.ANALYST
            async def score(self, data, context=None): return self._make_zero_result("test")

        agent = DummyAgent2()
        agent.record_sample()
        assert agent.state == AgentState.READY

        agent.mark_degraded("test reason")
        assert agent.state == AgentState.DEGRADED

        agent.mark_recovered()
        assert agent.state == AgentState.READY

    def test_warmup_result_has_flag(self) -> None:
        """Warmup result includes AGENT_WARMING_UP risk tag."""
        class DummyAgent3(BaseAgent):
            MIN_SAMPLES_TO_EMIT = 5
            @property
            def name(self) -> str: return "dummy3"
            @property
            def category(self): return AgentCategory.CONTEXT
            @property
            def tier(self): return AgentTier.ANALYST
            async def score(self, data, context=None): return self._make_zero_result("test")

        agent = DummyAgent3()
        result = agent._make_warmup_result()
        assert "AGENT_WARMING_UP" in result.risks
        assert "WARMING_UP" in result.explanation
