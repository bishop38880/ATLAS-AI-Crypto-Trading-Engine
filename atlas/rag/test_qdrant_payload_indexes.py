"""Unit tests for Qdrant payload keyword / integer index helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
from qdrant_client.http.exceptions import UnexpectedResponse

from atlas.rag.qdrant_payload_indexes import (
    INTEGER_INDEX_FIELDS,
    KEYWORD_INDEX_FIELDS,
    ensure_rag_signal_memory_payload_indexes,
    keyword_index_field_list,
)


def test_keyword_index_field_list_matches_constants() -> None:
    """Exported list matches the canonical tuple."""
    assert keyword_index_field_list() == list(KEYWORD_INDEX_FIELDS)


@pytest.mark.asyncio
async def test_ensure_indexes_handles_already_exists_http() -> None:
    """Duplicate index from Qdrant does not raise."""

    async def _raise_once(*_a: object, **_kw: object) -> None:
        raise UnexpectedResponse(
            status_code=409,
            reason_phrase="Conflict",
            content=b'{"status":{"error":"already exists"}}',
            headers=httpx.Headers({"content-type": "application/json"}),
        )

    client = AsyncMock()
    client.create_payload_index = AsyncMock(side_effect=_raise_once)

    await ensure_rag_signal_memory_payload_indexes(client, collection_name="rag_signal_memory")

    expected_calls = len(KEYWORD_INDEX_FIELDS) + len(INTEGER_INDEX_FIELDS)
    assert client.create_payload_index.await_count == expected_calls
