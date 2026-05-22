"""Tests for CrossCorrelationGrade enum — IM-1 Task 1.

Tests live alongside code (atlas/models/test_enums.py).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from atlas.models.enums import CrossCorrelationGrade


class TestCrossCorrelationGrade:
    """CrossCorrelationGrade enum is a str enum with three tiers."""

    def test_standard_value(self) -> None:
        """STANDARD grade has string value 'STANDARD'."""
        assert CrossCorrelationGrade.STANDARD == "STANDARD"
        assert CrossCorrelationGrade.STANDARD.value == "STANDARD"

    def test_elevated_value(self) -> None:
        """ELEVATED grade has string value 'ELEVATED'."""
        assert CrossCorrelationGrade.ELEVATED == "ELEVATED"
        assert CrossCorrelationGrade.ELEVATED.value == "ELEVATED"

    def test_extreme_value(self) -> None:
        """EXTREME grade has string value 'EXTREME'."""
        assert CrossCorrelationGrade.EXTREME == "EXTREME"
        assert CrossCorrelationGrade.EXTREME.value == "EXTREME"

    def test_is_str_enum(self) -> None:
        """CrossCorrelationGrade inherits from (str, Enum)."""
        assert isinstance(CrossCorrelationGrade.STANDARD, str)

    def test_exactly_three_members(self) -> None:
        """Enum has exactly three members."""
        assert len(CrossCorrelationGrade) == 3

    def test_string_coercion_roundtrip(self) -> None:
        """String value round-trips through enum constructor."""
        grade = CrossCorrelationGrade("STANDARD")
        assert grade is CrossCorrelationGrade.STANDARD

    def test_invalid_string_rejected(self) -> None:
        """Invalid string raises ValueError."""
        with pytest.raises(ValueError):
            CrossCorrelationGrade("INVALID")
