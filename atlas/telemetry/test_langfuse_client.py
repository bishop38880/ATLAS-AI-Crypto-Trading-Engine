"""Tests for Langfuse telemetry wrapper."""

import pytest
from unittest.mock import MagicMock, patch
import contextvars

from atlas.telemetry.langfuse_client import (
    LangfuseTelemetry,
    _DummyTrace,
    _DummySpan,
    _DummyGeneration,
    _DummyEvent,
    current_trace_id
)

@pytest.fixture
def telemetry():
    """Return a new instance of telemetry for testing."""
    # Reset singleton
    LangfuseTelemetry._instance = None
    return LangfuseTelemetry()

def test_telemetry_initialization_no_langfuse():
    """Test graceful degradation when Langfuse SDK is missing."""
    with patch('atlas.telemetry.langfuse_client.Langfuse', None):
        LangfuseTelemetry._instance = None
        t = LangfuseTelemetry()
        assert t._enabled is False
        assert isinstance(t.trace("test"), _DummyTrace)

def test_telemetry_initialization_with_langfuse(telemetry):
    """Test successful initialization."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        assert telemetry._enabled is True
        assert telemetry._lf is not None

def test_trace_creation_fallback(telemetry):
    """Test trace creation falls back to dummy on error."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        telemetry._lf.trace.side_effect = Exception("Network error")
        
        trace = telemetry.trace("test_trace")
        assert isinstance(trace, _DummyTrace)

def test_span_with_current_trace_id(telemetry):
    """Test span uses implicit contextvar trace_id."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        token = current_trace_id.set("implicit-123")
        try:
            telemetry.span("test_span")
            telemetry._lf.span.assert_called_once_with(trace_id="implicit-123", name="test_span")
        finally:
            current_trace_id.reset(token)

def test_generation_with_explicit_trace_id(telemetry):
    """Test generation uses explicitly passed trace_id over contextvar."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        token = current_trace_id.set("implicit-123")
        try:
            telemetry.generation("test_gen", trace_id="explicit-456")
            telemetry._lf.generation.assert_called_once_with(trace_id="explicit-456", name="test_gen")
        finally:
            current_trace_id.reset(token)

def test_event_missing_trace_id(telemetry):
    """Test event returns dummy if no trace_id is available."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        # No context var set, no trace_id passed
        evt = telemetry.event("test_evt")
        assert isinstance(evt, _DummyEvent)
        telemetry._lf.event.assert_not_called()

def test_dummy_objects_do_not_raise():
    """Test dummy objects can be chained and updated without error."""
    trace = _DummyTrace()
    span = trace.span()
    gen = trace.generation()
    evt = trace.event()
    
    trace.update(output="test")
    span.update(metadata={"key": "val"})
    span.end(output="end")
    span.event()
    gen.end(output="gen")
    # Verify dummy objects returned correct types through chaining
    assert isinstance(trace, _DummyTrace)
    assert isinstance(span, _DummySpan)
    assert isinstance(gen, _DummyGeneration)
    assert isinstance(evt, _DummyEvent)

def test_flush_handles_errors(telemetry):
    """Test flush gracefully handles errors."""
    with patch('atlas.telemetry.langfuse_client.Langfuse') as mock_lf:
        telemetry._initialize()
        telemetry._lf.flush.side_effect = Exception("Flush timeout")
        
        # Should not raise
        telemetry.flush()
        telemetry._lf.flush.assert_called_once()
