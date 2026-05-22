"""Langfuse telemetry client wrapper for ATLAS.

Provides a singleton Langfuse client that gracefully degrades if the
Langfuse sidecar is unreachable. Exposes wrappers for traces, spans,
and generations.
"""

from __future__ import annotations

from typing import Any, Optional
import functools
import contextvars

from loguru import logger

try:
    from langfuse import Langfuse
except ImportError:
    Langfuse = None

from atlas.shared.config import PolarisSettings

# Context variable to hold the current trace ID implicitly
current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_trace_id", default=None)


class LangfuseTelemetry:
    """Singleton wrapper for Langfuse observability."""

    _instance: Optional['LangfuseTelemetry'] = None

    def __new__(cls) -> 'LangfuseTelemetry':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self) -> None:
        self._enabled = False
        self._lf = None

        if Langfuse is None:
            logger.warning("Langfuse SDK not installed. Telemetry disabled.")
            return

        settings = PolarisSettings()  # type: ignore[call-arg]
        
        try:
            # We don't want Langfuse to crash the application if host is down.
            # The python SDK is async in the background and gracefully handles network issues,
            # but we still guard the initialization.
            self._lf = Langfuse(
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                public_key=settings.langfuse_public_key,
                host=settings.langfuse_host,
                debug=False,
            )
            # Perform a quick auth check (this uses httpx synchronously by default in langfuse check, 
            # but it catches errors. Actually `auth_check()` makes a sync request. Let's just assume enabled.
            # We will rely on background thread handling errors.)
            self._enabled = True
            logger.info("Langfuse telemetry initialized.")
        except Exception as e:
            logger.error("Failed to initialize Langfuse telemetry | error={}", str(e))

    def trace(self, name: str, id: Optional[str] = None, **kwargs: Any) -> Any:
        """Create a Langfuse trace."""
        if not self._enabled or self._lf is None:
            return _DummyTrace()
        try:
            return self._lf.trace(name=name, id=id, **kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Langfuse trace creation failed | error={}", str(e))
            return _DummyTrace()

    def span(self, name: str, trace_id: Optional[str] = None, **kwargs: Any) -> Any:
        """Create a span, linked to trace_id or current_trace_id."""
        if not self._enabled or self._lf is None:
            return _DummySpan()
        t_id = trace_id or current_trace_id.get()
        if not t_id:
            return _DummySpan()
        try:
            return self._lf.span(trace_id=t_id, name=name, **kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Langfuse span creation failed | error={}", str(e))
            return _DummySpan()

    def generation(self, name: str, trace_id: Optional[str] = None, **kwargs: Any) -> Any:
        """Create a generation, linked to trace_id or current_trace_id."""
        if not self._enabled or self._lf is None:
            return _DummyGeneration()
        t_id = trace_id or current_trace_id.get()
        if not t_id:
            return _DummyGeneration()
        try:
            return self._lf.generation(trace_id=t_id, name=name, **kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Langfuse generation creation failed | error={}", str(e))
            return _DummyGeneration()

    def event(self, name: str, trace_id: Optional[str] = None, **kwargs: Any) -> Any:
        """Create an event, linked to trace_id or current_trace_id."""
        if not self._enabled or self._lf is None:
            return _DummyEvent()
        t_id = trace_id or current_trace_id.get()
        if not t_id:
            return _DummyEvent()
        try:
            return self._lf.event(trace_id=t_id, name=name, **kwargs)  # type: ignore[attr-defined]
        except Exception as e:
            logger.warning("Langfuse event creation failed | error={}", str(e))
            return _DummyEvent()

    def flush(self) -> None:
        """Flush the Langfuse client."""
        if self._enabled and self._lf:
            try:
                self._lf.flush()
            except Exception as e:
                logger.warning("Langfuse flush failed", str(e))


class _DummyTrace:
    """A dummy trace object that ignores calls, for graceful degradation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def span(self, *args: Any, **kwargs: Any) -> '_DummySpan':
        return _DummySpan()

    def generation(self, *args: Any, **kwargs: Any) -> '_DummyGeneration':
        return _DummyGeneration()

    def event(self, *args: Any, **kwargs: Any) -> '_DummyEvent':
        return _DummyEvent()
        
    def update(self, *args: Any, **kwargs: Any) -> None:
        pass


class _DummySpan:
    def update(self, *args: Any, **kwargs: Any) -> None:
        pass
        
    def end(self, *args: Any, **kwargs: Any) -> None:
        pass
        
    def event(self, *args: Any, **kwargs: Any) -> '_DummyEvent':
        return _DummyEvent()


class _DummyGeneration:
    def end(self, *args: Any, **kwargs: Any) -> None:
        pass

    def update(self, *args: Any, **kwargs: Any) -> None:
        pass


class _DummyEvent:
    pass


telemetry = LangfuseTelemetry()
