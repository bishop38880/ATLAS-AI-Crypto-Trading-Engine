"""Tests for PROMETHEUS schema version validation gate.

Covers:
    1. Valid version is accepted
    2. Missing schema_version is rejected
    3. Unknown version is rejected
    4. Allowed versions set is frozen and contains expected entries
"""

from __future__ import annotations

from prometheus.shared.schema_version import (
    ALLOWED_SCHEMA_VERSIONS,
    validate_schema_version,
)


# ── Test 1 — Valid version accepted ──────────────────────────────────
def test_valid_version_accepted() -> None:
    """Signal with a known schema_version must pass validation."""
    payload = {"schema_version": "2.0.0", "signal_id": "test-001"}
    assert validate_schema_version(payload) is True


# ── Test 2 — Missing version rejected ───────────────────────────────
def test_missing_version_rejected() -> None:
    """Signal without schema_version must be rejected."""
    payload = {"signal_id": "test-002"}
    assert validate_schema_version(payload) is False


# ── Test 3 — Unknown version rejected ───────────────────────────────
def test_unknown_version_rejected() -> None:
    """Signal with an unrecognised schema_version must be rejected."""
    payload = {"schema_version": "99.0.0", "signal_id": "test-003"}
    assert validate_schema_version(payload) is False


# ── Test 4 — Allowed set is frozen ───────────────────────────────────
def test_allowed_versions_frozen() -> None:
    """ALLOWED_SCHEMA_VERSIONS must be a frozenset."""
    assert isinstance(ALLOWED_SCHEMA_VERSIONS, frozenset)
    assert "2.0.0" in ALLOWED_SCHEMA_VERSIONS
