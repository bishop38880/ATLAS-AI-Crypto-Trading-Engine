"""Shared base models and configurations for PROMETHEUS."""

from typing import Annotated
from decimal import Decimal, InvalidOperation
from pydantic import ConfigDict, AfterValidator
from pydantic.alias_generators import to_camel

def _validate_decimal_string(v: str) -> str:
    try:
        Decimal(v)
    except InvalidOperation as e:
        raise ValueError(f"not a valid Decimal string: {v!r}") from e
    return v

StringDecimal = Annotated[str, AfterValidator(_validate_decimal_string)]

_BaseConfig = ConfigDict(
    frozen=True,
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
)
