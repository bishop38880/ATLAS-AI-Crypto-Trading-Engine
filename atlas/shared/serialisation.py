"""High-performance JSON serialization using msgspec."""

from typing import Any, TypeVar

import msgspec
from pydantic import BaseModel

T = TypeVar("T")


def encode_json(obj: Any) -> bytes:
    """Encode any supported object to JSON bytes.
    
    Wraps msgspec.json.encode for performance.
    """
    return msgspec.json.encode(obj)


def decode_json(data: bytes, type: type[T] = Any) -> T:  # type: ignore[assignment]
    """Decode JSON bytes to a specified type.
    
    Wraps msgspec.json.decode. Defaults to un-typed dictionary
    if no type is provided.
    """
    return msgspec.json.decode(data, type=type)


def pydantic_to_msgspec(model: BaseModel) -> bytes:
    """Encode a Pydantic model to JSON bytes quickly.
    
    Uses Pydantic's model_dump(mode='json') to handle native types
    like Decimal before passing to msgspec.
    """
    return msgspec.json.encode(model.model_dump(mode="json"))
