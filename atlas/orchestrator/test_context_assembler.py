"""Tests for ContextAssembler."""

from atlas.agents.base import AgentResult, SignalDirection
from atlas.models.signal import SubSignalResult
from atlas.orchestrator.context_assembler import ContextAssembler

def test_context_assembler_empty_results() -> None:
    """Test assembling with empty agent results produces valid output."""
    mtf_context = {"1h": "bullish", "4h": "bearish"}
    result = ContextAssembler.build_matrix_string("BTCUSDT", 120, [], mtf_context)
    
    assert "ASSET: BTCUSDT" in result
    assert "CURRENT CONFLUENCE SCORE: 120/220" in result
    assert "MULTI-TIMEFRAME CONCORDANCE:" in result
    assert "- 15m: Unknown" in result
    assert "- 1h: bullish" in result
    assert "- 4h: bearish" in result
    assert "AGENT SUB-SIGNAL MATRIX:\n{}" in result

def test_context_assembler_with_results() -> None:
    """Test assembling with populated agent results."""
    ar = AgentResult(
        agent_name="technical",
        score=45,
        max_score=45,
        weight=0.8,
        direction=SignalDirection.BULLISH,
        explanation="Test explanation",
        convergences=[],
        risks=[],
        veto=False,
        sub_signals={
            "rsi": SubSignalResult(
                value="35.5", flag="NEUTRAL", metadata={"trend": "up"}
            )
        }
    )
    
    mtf_context = {"15m": "bullish", "1d": "bearish"}
    result = ContextAssembler.build_matrix_string("ETHUSDT", 180, [ar], mtf_context)
    
    assert "ASSET: ETHUSDT" in result
    assert "CURRENT CONFLUENCE SCORE: 180/220" in result
    
    # Check that matrix_json is correct
    assert '"technical"' in result
    assert '"score_contribution":45' in result
    assert '"weight":0.8' in result
    assert '"signals"' in result
    assert '"rsi"' in result
    assert '"value":"35.5"' in result
    assert '"flag":"NEUTRAL"' in result
    assert '"metadata":{"trend":"up"}' in result

def test_build_sub_signal_matrix_dict_structure() -> None:
    """Test dict structure of sub signal matrix."""
    ar = AgentResult(
        agent_name="funding",
        score=10,
        max_score=20,
        weight=0.5,
        direction=SignalDirection.BEARISH,
        explanation="Test explanation",
        convergences=[],
        risks=[],
        veto=False,
        sub_signals={
            "funding_rate": SubSignalResult(
                value="-0.01%", flag="NEGATIVE", metadata={}
            )
        }
    )
    
    matrix = ContextAssembler._build_sub_signal_matrix([ar])
    assert "funding" in matrix
    assert matrix["funding"]["score_contribution"] == 10
    assert matrix["funding"]["weight"] == 0.5
    assert matrix["funding"]["signals"]["funding_rate"]["value"] == "-0.01%"
    assert matrix["funding"]["signals"]["funding_rate"]["flag"] == "NEGATIVE"
