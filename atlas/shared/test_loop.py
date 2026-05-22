"""Tests for the event loop configuration."""

import asyncio

from atlas.shared import loop  # noqa: F401


def test_uvloop_is_active() -> None:
    """Verify asyncio uses the uvloop event loop policy."""
    policy = asyncio.get_event_loop_policy()
    assert "uvloop" in policy.__class__.__module__.lower()
