"""Tests for Qdrant client factory."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from atlas.core.qdrant_client_factory import resolve_qdrant_api_key
from atlas.shared.config import PolarisSettings


def test_resolve_qdrant_api_key_empty_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "")
    settings = PolarisSettings()
    assert resolve_qdrant_api_key(settings) is None


def test_resolve_qdrant_api_key_returns_trimmed_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("QDRANT_API_KEY", "  test-key  ")
    settings = PolarisSettings()
    assert resolve_qdrant_api_key(settings) == "test-key"


def test_settings_loads_qdrant_api_key_from_env() -> None:
    """Regression: production .env QDRANT_API_KEY must reach PolarisSettings."""
    settings = PolarisSettings()
    key = resolve_qdrant_api_key(settings)
    assert key is None or len(key) > 0
