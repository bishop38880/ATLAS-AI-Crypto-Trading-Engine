"""Signal schema version validation for PROMETHEUS.

PROMETHEUS must reject any signal from ATLAS that carries an unknown
schema_version.  This module provides the validation function and the
set of versions this PROMETHEUS deployment can safely consume.

Architecture:
    - Imported by the signal subscriber before routing to any execution
      path (paper trading, live execution, reconciliation).
    - On version mismatch: log a CRITICAL error and discard the signal.
      Never attempt partial interpretation.

See also:
    - atlas/models/signal.py — SIGNAL_SCHEMA_VERSION constant
    - POLARIS Audit v1.0, Finding S1.3
"""

from __future__ import annotations

from typing import Any

from loguru import logger

# ── Allowed schema versions ────────────────────────────────────────
# Add new versions here when ATLAS bumps SIGNAL_SCHEMA_VERSION and
# PROMETHEUS has been updated to handle the new fields.
ALLOWED_SCHEMA_VERSIONS: frozenset[str] = frozenset({
    "2.0.0",
})


def validate_schema_version(payload: dict[str, Any]) -> bool:
    """Check that the signal payload carries an allowed schema_version.

    Args:
        payload: Deserialised signal JSON from Redis.

    Returns:
        True if the version is allowed, False otherwise.
    """
    version = payload.get("schema_version")

    if version is None:
        logger.critical(
            "signal_rejected | reason=missing_schema_version | signal_id={}",
            payload.get("signal_id", "unknown"),
        )
        return False

    if version not in ALLOWED_SCHEMA_VERSIONS:
        logger.critical(
            "signal_rejected | reason=unknown_schema_version"
            " | version={} | allowed={} | signal_id={}",
            version,
            sorted(ALLOWED_SCHEMA_VERSIONS),
            payload.get("signal_id", "unknown"),
        )
        return False

    return True
