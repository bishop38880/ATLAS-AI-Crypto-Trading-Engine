"""Tests for PolarisSettings defaults."""

from __future__ import annotations

from atlas.shared.config import PolarisSettings


def test_confluence_cycle_interval_default_is_fifteen_minutes() -> None:
    """Autonomous pipeline spacing defaults to 900s so confluence refreshes every 15 minutes."""
    settings = PolarisSettings()
    assert settings.confluence_cycle_interval_seconds == 900


def test_openapi_catalog_disabled_by_default() -> None:
    """Swagger/OpenAPI stay off unless explicitly enabled so HTTP surfaces are not published."""
    settings = PolarisSettings()
    assert settings.atlas_expose_openapi is False
