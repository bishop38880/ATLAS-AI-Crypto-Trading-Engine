"""Root pytest configuration for the ATLAS monorepo.

Third-party pytest plugins (e.g. LangSmith) may enable OpenTelemetry OTLP
export to localhost:4318 by default. CI and local runs rarely have a collector,
which spams connection errors at session teardown. Disable the SDK unless the
environment already opted in explicitly.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Ensure OTLP export is off for the test process unless explicitly enabled."""
    if os.environ.get("OTEL_SDK_DISABLED", "").lower() in ("true", "1", "yes"):
        return
    if os.environ.get("ENABLE_OTEL_IN_TESTS", "").lower() in ("true", "1", "yes"):
        return
    os.environ["OTEL_SDK_DISABLED"] = "true"
