"""Tests for RAG retrieval telemetry helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.rag.retrieval_telemetry import (
    append_retrieval_event,
    build_retrieval_event_dict,
    list_recent_retrieval_events,
)


def test_build_retrieval_event_shape() -> None:
    """Telemetry dict includes scores for each hit."""
    event = build_retrieval_event_dict(
        asset="SOLUSDT",
        query_text="market context for SOLUSDT",
        retrieval_depth="default",
        hit_count=1,
        hits=[
            {
                "documentId": "pt-1",
                "similarityScore": 0.91,
                "finalScore": 0.88,
                "asset": "SOLUSDT",
                "signalDecision": "Hold",
                "preview": "Example memory",
                "userTags": ["regime_misclassification"],
            },
        ],
    )
    assert event["asset"] == "SOLUSDT"
    assert event["hitCount"] == 1
    assert len(event["hits"]) == 1
    assert event["hits"][0]["documentId"] == "pt-1"


@pytest.mark.asyncio()
async def test_list_recent_decodes_json() -> None:
    """lrange payloads decode into dicts."""
    redis = AsyncMock()
    ev = build_retrieval_event_dict(
        asset="ETHUSDT",
        query_text="q",
        retrieval_depth="shallow",
        hit_count=0,
        hits=[],
    )
    import msgspec

    redis.lrange = AsyncMock(return_value=[msgspec.json.encode(ev)])
    out = await list_recent_retrieval_events(redis, limit=5)
    assert len(out) == 1
    assert out[0]["asset"] == "ETHUSDT"


@pytest.mark.asyncio()
async def test_append_uses_pipeline() -> None:
    """append_retrieval_event uses LPUSH + LTRIM pipeline."""
    import msgspec

    redis = AsyncMock()
    pipe = AsyncMock()
    pipe.lpush = MagicMock()
    pipe.ltrim = MagicMock()
    pipe.execute = AsyncMock(return_value=[1, True])
    redis.pipeline = MagicMock(return_value=pipe)

    ev = build_retrieval_event_dict(
        asset="X",
        query_text="q",
        retrieval_depth="default",
        hit_count=0,
        hits=[],
    )
    await append_retrieval_event(redis, ev)
    redis.pipeline.assert_called_once()
    pipe.lpush.assert_called_once()
    pipe.ltrim.assert_called_once()
    pipe.execute.assert_awaited_once()
    lpush_args = pipe.lpush.call_args[0]
    assert lpush_args[0] == "polaris:rag:retrieval_events"
    decoded = msgspec.json.decode(lpush_args[1])
    assert decoded["asset"] == "X"


@pytest.mark.asyncio()
async def test_list_recent_empty_on_redis_error() -> None:
    """Read failures return []."""
    redis = AsyncMock()
    redis.lrange = AsyncMock(side_effect=RuntimeError("down"))
    out = await list_recent_retrieval_events(redis, limit=3)
    assert out == []
