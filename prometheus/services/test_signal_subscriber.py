"""Tests for the validated ATLAS signal subscriber."""

from __future__ import annotations

from unittest.mock import AsyncMock

import msgspec
import pytest

from prometheus.services.signal_subscriber import AtlasSignalSubscriber


def _message(payload: dict[str, object]) -> dict[str, object]:
    return {
        "type": "pmessage",
        "data": msgspec.json.encode(payload),
    }


@pytest.mark.asyncio
async def test_subscriber_dispatches_valid_unexpired_signal() -> None:
    """Valid ATLAS signals reach the execution callback."""
    handler = AsyncMock()
    subscriber = AtlasSignalSubscriber(AsyncMock(), handler)
    payload = {
        "schema_version": "2.0.0",
        "signal_id": "sig-valid",
        "expires_at": "2999-01-01T00:00:00Z",
    }

    await subscriber._handle_message(_message(payload))

    handler.assert_awaited_once()
    assert handler.await_args.args[0]["signal_id"] == "sig-valid"


@pytest.mark.asyncio
async def test_subscriber_rejects_expired_signal() -> None:
    """Expired signals are discarded before execution callbacks."""
    handler = AsyncMock()
    subscriber = AtlasSignalSubscriber(AsyncMock(), handler)
    payload = {
        "schema_version": "2.0.0",
        "signal_id": "sig-expired",
        "expires_at": "2000-01-01T00:00:00Z",
    }

    await subscriber._handle_message(_message(payload))

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscriber_rejects_unknown_schema_version() -> None:
    """Unknown signal schemas are discarded before execution callbacks."""
    handler = AsyncMock()
    subscriber = AtlasSignalSubscriber(AsyncMock(), handler)
    payload = {
        "schema_version": "99.0.0",
        "signal_id": "sig-unknown-schema",
        "expires_at": "2999-01-01T00:00:00Z",
    }

    await subscriber._handle_message(_message(payload))

    handler.assert_not_awaited()
