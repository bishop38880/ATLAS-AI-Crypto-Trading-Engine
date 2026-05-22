"""Tests for PROMETHEUS pre-execution signal validation."""

from __future__ import annotations

from datetime import datetime, timezone

import msgspec

from prometheus.shared.signal_guard import (
    decode_signal_payload,
    validate_pre_execution_signal,
    validate_signal_not_expired,
)


def test_decode_signal_payload_accepts_msgspec_json() -> None:
    """Valid msgspec JSON signal payload decodes to a dictionary."""
    raw_payload = msgspec.json.encode({
        "schema_version": "2.0.0",
        "signal_id": "sig-001",
        "expires_at": "2026-04-28T13:30:00+00:00",
    })

    payload = decode_signal_payload(raw_payload)

    assert payload is not None
    assert payload["signal_id"] == "sig-001"


def test_decode_signal_payload_rejects_invalid_json() -> None:
    """Invalid signal payloads are rejected before execution."""
    assert decode_signal_payload(b"not-json") is None


def test_pre_execution_signal_accepts_valid_future_expiry() -> None:
    """Known schema and future expiry passes the guard."""
    payload = {
        "schema_version": "2.0.0",
        "signal_id": "sig-002",
        "expires_at": "2026-04-28T13:30:00Z",
    }
    now = datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc)

    assert validate_pre_execution_signal(payload, now=now) is True


def test_pre_execution_signal_rejects_unknown_schema() -> None:
    """Unknown schema versions are rejected before expiry handling."""
    payload = {
        "schema_version": "99.0.0",
        "signal_id": "sig-003",
        "expires_at": "2026-04-28T13:30:00Z",
    }
    now = datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc)

    assert validate_pre_execution_signal(payload, now=now) is False


def test_signal_expiry_rejects_missing_value() -> None:
    """Signals without expires_at cannot enter execution paths."""
    payload = {"schema_version": "2.0.0", "signal_id": "sig-004"}

    assert validate_signal_not_expired(payload) is False


def test_signal_expiry_rejects_expired_value() -> None:
    """Expired signals are rejected before any execution path."""
    payload = {
        "schema_version": "2.0.0",
        "signal_id": "sig-005",
        "expires_at": "2026-04-28T12:59:59Z",
    }
    now = datetime(2026, 4, 28, 13, 0, tzinfo=timezone.utc)

    assert validate_signal_not_expired(payload, now=now) is False


def test_signal_expiry_rejects_invalid_value() -> None:
    """Invalid timestamps fail closed."""
    payload = {
        "schema_version": "2.0.0",
        "signal_id": "sig-006",
        "expires_at": "tomorrow",
    }

    assert validate_signal_not_expired(payload) is False
