"""Pre-execution validation for ATLAS signals consumed by PROMETHEUS."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import msgspec
from loguru import logger

from prometheus.shared.schema_version import validate_schema_version


def decode_signal_payload(raw_payload: bytes | str) -> dict[str, Any] | None:
    """Decode a Redis signal payload into a dictionary."""
    try:
        payload = msgspec.json.decode(raw_payload, type=dict[str, Any])
    except (msgspec.DecodeError, msgspec.ValidationError) as exc:
        logger.critical("signal_rejected | reason=decode_failed | error={}", str(exc))
        return None

    return payload


def validate_signal_not_expired(
    payload: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Return True when the signal has an unexpired ``expires_at`` value."""
    expires_at_raw = payload.get("expires_at")
    signal_id = payload.get("signal_id", "unknown")
    if not isinstance(expires_at_raw, str):
        logger.critical(
            "signal_rejected | reason=missing_expires_at | signal_id={}",
            signal_id,
        )
        return False

    expires_at = _parse_utc_datetime(expires_at_raw)
    if expires_at is None:
        logger.critical(
            "signal_rejected | reason=invalid_expires_at | signal_id={} | expires_at={}",
            signal_id,
            expires_at_raw,
        )
        return False

    current_time = now or datetime.now(timezone.utc)
    if _as_utc(expires_at) <= _as_utc(current_time):
        logger.critical(
            "signal_rejected | reason=expired | signal_id={} | expires_at={}",
            signal_id,
            expires_at_raw,
        )
        return False

    return True


def validate_pre_execution_signal(
    payload: dict[str, Any],
    now: datetime | None = None,
) -> bool:
    """Validate schema version and expiry before any execution path."""
    if not validate_schema_version(payload):
        return False

    return validate_signal_not_expired(payload, now=now)


def _parse_utc_datetime(value: str) -> datetime | None:
    """Parse ISO 8601 timestamps with ``Z`` or explicit offsets."""
    try:
        normalised_value = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalised_value)
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)

    return value.astimezone(timezone.utc)
