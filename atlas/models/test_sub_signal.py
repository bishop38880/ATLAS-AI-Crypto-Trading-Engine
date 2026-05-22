"""Tests for SubSignalResult — IM-1 Task 6.

Tests live alongside code (atlas/models/test_sub_signal.py).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.models.signal import SubSignalResult


class TestSubSignalResultFlagValidation:
    """SubSignalResult.flag must be uppercase."""

    def test_lowercase_flag_rejected(self) -> None:
        """A lowercase flag raises ValidationError."""
        with pytest.raises(ValidationError, match="flag must be uppercase"):
            SubSignalResult(
                value="+2.8 SD",
                flag="extreme_short_crowding",
            )

    def test_mixed_case_flag_rejected(self) -> None:
        """A mixed-case flag raises ValidationError."""
        with pytest.raises(ValidationError, match="flag must be uppercase"):
            SubSignalResult(
                value="+2.8 SD",
                flag="Extreme_Short_Crowding",
            )

    def test_upper_snake_case_flag_accepted(self) -> None:
        """Upper-snake-case flag like EXTREME_SHORT_CROWDING is valid."""
        result = SubSignalResult(
            value="+2.8 SD",
            flag="EXTREME_SHORT_CROWDING",
        )
        assert result.flag == "EXTREME_SHORT_CROWDING"

    def test_simple_uppercase_flag_accepted(self) -> None:
        """Simple uppercase flag like BULLISH is valid."""
        result = SubSignalResult(
            value="0.04%",
            flag="BULLISH",
        )
        assert result.flag == "BULLISH"


class TestSubSignalResultMetadata:
    """SubSignalResult.metadata defaults to empty dict."""

    def test_default_metadata_empty(self) -> None:
        """Metadata defaults to an empty dict."""
        result = SubSignalResult(
            value="+2.8 SD",
            flag="HIGH_ZSCORE",
        )
        assert result.metadata == {}

    def test_custom_metadata_preserved(self) -> None:
        """Custom metadata dict is preserved."""
        meta = {"zscore": 2.8, "is_extreme": True, "source": "coinalyze"}
        result = SubSignalResult(
            value="+2.8 SD",
            flag="HIGH_ZSCORE",
            metadata=meta,
        )
        assert result.metadata["zscore"] == 2.8
        assert result.metadata["is_extreme"] is True
        assert result.metadata["source"] == "coinalyze"


class TestSubSignalResultFrozen:
    """SubSignalResult is immutable (frozen=True)."""

    def test_is_frozen(self) -> None:
        """Attribute reassignment raises ValidationError."""
        result = SubSignalResult(
            value="+2.8 SD",
            flag="HIGH_ZSCORE",
        )
        with pytest.raises(ValidationError):
            result.value = "changed"  # type: ignore[misc]
