"""Alternative.me Fear & Greed data models.

Architecture Invariants:
- Frozen Pydantic v2 models.
- Native Python types (int for value).
- msgspec compatible.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FearAndGreedData(BaseModel):
    """Inner data for Fear and Greed Index.
    
    API returns values as strings, but we cast to int for engine consumption.
    """

    model_config = ConfigDict(frozen=True)

    value: int
    value_classification: str
    timestamp: int

    @field_validator("value", mode="before")
    @classmethod
    def cast_string_to_int(cls, v: str | int) -> int:
        """Safely cast string '40' to native int 40."""
        if isinstance(v, str):
            return int(v)
        return v


class AlternativeMeSnapshot(BaseModel):
    """Top-level snapshot for Alternative.me provider.
    
    Includes status and stale flags for graceful degradation.
    """

    model_config = ConfigDict(frozen=True)

    data: FearAndGreedData | None = None
    status: Literal["HEALTHY", "DEGRADED", "UNAVAILABLE"] = "HEALTHY"
    stale: bool = False
    error: str | None = None
