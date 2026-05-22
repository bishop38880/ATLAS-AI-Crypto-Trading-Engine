"""Tests for HydraStreamListener — Redis Streams cascade ingestion.

Tests live alongside code at atlas/providers/hydra/test_listener.py.
Never in a top-level tests/ directory.

Coverage:
    - msgspec decode → Domain model validation pipeline
    - Buffer update on valid message
    - get_latest_event() asset-specific and cross-asset
    - Heartbeat discipline: HEALTHY within threshold, DEGRADED beyond
    - Strict tier validation (Literal[1,2,3,4] rejects 0 and 5)
    - total_liquidation_usd is Decimal, not float
    - Malformed JSON → provider marked DEGRADED
"""

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import msgspec
import pytest
from pydantic import ValidationError

from atlas.providers.hydra.listener import (
    HydraCascadeEvent,
    HydraStreamListener,
    _decode_cascade_event,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event_payload(
    event_id: str = "evt-001",
    asset: str = "BTC/USDT",
    tier: int = 3,
    exchanges: list[str] | None = None,
    total_liquidation_usd_str: str = "15000000.50",
    timestamp_iso: str = "2026-04-21T14:30:00+00:00",
) -> bytes:
    """Build a raw JSON bytes payload for a cascade event.

    Returns:
        JSON-encoded bytes suitable for Redis Streams.
    """
    if exchanges is None:
        exchanges = ["binance", "okx"]
    payload = {
        "event_id": event_id,
        "asset": asset,
        "tier": tier,
        "exchanges": exchanges,
        "total_liquidation_usd_str": total_liquidation_usd_str,
        "timestamp_iso": timestamp_iso,
    }
    return msgspec.json.encode(payload)


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock async Redis client."""
    return AsyncMock()


@pytest.fixture
def listener(mock_redis: AsyncMock) -> HydraStreamListener:
    """Create a HydraStreamListener with a mock Redis client."""
    return HydraStreamListener(mock_redis)


# ---------------------------------------------------------------------------
# HydraCascadeEvent model tests
# ---------------------------------------------------------------------------


class TestHydraCascadeEventModel:
    """Tests for the HydraCascadeEvent Pydantic model."""

    def test_valid_event_creation(self) -> None:
        """Valid fields produce a frozen event instance."""
        event = HydraCascadeEvent(
            event_id="evt-001",
            asset="BTC/USDT",
            tier=3,
            exchanges=["binance", "okx"],
            total_liquidation_usd=Decimal("15000000.50"),
            timestamp=datetime(
                2026, 4, 21, 14, 30, tzinfo=timezone.utc,
            ),
        )
        assert event.event_id == "evt-001"
        assert event.asset == "BTC/USDT"
        assert event.tier == 3

    def test_total_liquidation_usd_is_decimal(self) -> None:
        """total_liquidation_usd must be Decimal, not float."""
        event = HydraCascadeEvent(
            event_id="evt-002",
            asset="ETH/USDT",
            tier=1,
            exchanges=["binance"],
            total_liquidation_usd=Decimal("5000000.00"),
            timestamp=datetime(
                2026, 4, 21, 14, 30, tzinfo=timezone.utc,
            ),
        )
        assert isinstance(
            event.total_liquidation_usd, Decimal,
        )

    def test_tier_0_rejected(self) -> None:
        """tier=0 is not in Literal[1,2,3,4] — raises ValidationError."""
        with pytest.raises(ValidationError):
            HydraCascadeEvent(
                event_id="evt-bad",
                asset="BTC/USDT",
                tier=0,  # type: ignore[arg-type]
                exchanges=["binance"],
                total_liquidation_usd=Decimal("100.00"),
                timestamp=datetime(
                    2026, 4, 21, 14, 30, tzinfo=timezone.utc,
                ),
            )

    def test_tier_5_rejected(self) -> None:
        """tier=5 is not in Literal[1,2,3,4] — raises ValidationError."""
        with pytest.raises(ValidationError):
            HydraCascadeEvent(
                event_id="evt-bad",
                asset="BTC/USDT",
                tier=5,  # type: ignore[arg-type]
                exchanges=["binance"],
                total_liquidation_usd=Decimal("100.00"),
                timestamp=datetime(
                    2026, 4, 21, 14, 30, tzinfo=timezone.utc,
                ),
            )

    def test_event_is_frozen(self) -> None:
        """HydraCascadeEvent instances are immutable."""
        event = HydraCascadeEvent(
            event_id="evt-003",
            asset="SOL/USDT",
            tier=2,
            exchanges=["bybit"],
            total_liquidation_usd=Decimal("2000000.00"),
            timestamp=datetime(
                2026, 4, 21, 14, 30, tzinfo=timezone.utc,
            ),
        )
        with pytest.raises(Exception):
            event.asset = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Decode function tests
# ---------------------------------------------------------------------------


class TestDecodeFunction:
    """Tests for the _decode_cascade_event helper."""

    def test_decode_valid_json(self) -> None:
        """Valid JSON bytes decode to HydraCascadeEvent."""
        raw = _make_event_payload()
        event = _decode_cascade_event(raw)
        assert event.event_id == "evt-001"
        assert event.asset == "BTC/USDT"
        assert event.tier == 3
        assert isinstance(
            event.total_liquidation_usd, Decimal,
        )

    def test_decode_invalid_json_raises(self) -> None:
        """Malformed JSON raises msgspec.DecodeError."""
        with pytest.raises(msgspec.DecodeError):
            _decode_cascade_event(b"not json")

    def test_decode_missing_field_raises(self) -> None:
        """Missing required field raises msgspec.ValidationError."""
        partial = msgspec.json.encode(
            {"event_id": "evt-partial", "asset": "BTC/USDT"},
        )
        with pytest.raises(msgspec.ValidationError):
            _decode_cascade_event(partial)

    def test_decode_bad_tier_raises(self) -> None:
        """tier=0 in JSON payload raises msgspec.ValidationError."""
        raw = _make_event_payload(tier=0)
        with pytest.raises(msgspec.ValidationError):
            _decode_cascade_event(raw)


# ---------------------------------------------------------------------------
# Listener buffer tests
# ---------------------------------------------------------------------------


class TestListenerBuffer:
    """Tests for the in-memory event buffer."""

    def test_buffer_empty_returns_none(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """get_latest_event returns None when buffer is empty."""
        assert listener.get_latest_event() is None
        assert listener.get_latest_event("BTC/USDT") is None

    @pytest.mark.asyncio
    async def test_process_message_updates_buffer(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Valid message updates the asset buffer."""
        raw = _make_event_payload(asset="BTC/USDT")
        await listener._process_message(raw)
        event = listener.get_latest_event("BTC/USDT")
        assert event is not None
        assert event.asset == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_get_latest_event_by_asset(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """get_latest_event(asset) returns the correct event."""
        await listener._process_message(
            _make_event_payload(
                event_id="btc-1", asset="BTC/USDT",
            ),
        )
        await listener._process_message(
            _make_event_payload(
                event_id="eth-1", asset="ETH/USDT",
            ),
        )
        btc = listener.get_latest_event("BTC/USDT")
        eth = listener.get_latest_event("ETH/USDT")
        assert btc is not None
        assert btc.event_id == "btc-1"
        assert eth is not None
        assert eth.event_id == "eth-1"

    @pytest.mark.asyncio
    async def test_get_latest_event_no_asset_returns_newest(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """get_latest_event() with no asset returns most recent."""
        await listener._process_message(
            _make_event_payload(
                event_id="old",
                asset="BTC/USDT",
                timestamp_iso="2026-04-21T14:00:00+00:00",
            ),
        )
        await listener._process_message(
            _make_event_payload(
                event_id="new",
                asset="ETH/USDT",
                timestamp_iso="2026-04-21T15:00:00+00:00",
            ),
        )
        latest = listener.get_latest_event()
        assert latest is not None
        assert latest.event_id == "new"

    @pytest.mark.asyncio
    async def test_buffer_overwrites_same_asset(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Newer event for same asset overwrites the old one."""
        await listener._process_message(
            _make_event_payload(
                event_id="old-btc", asset="BTC/USDT",
            ),
        )
        await listener._process_message(
            _make_event_payload(
                event_id="new-btc", asset="BTC/USDT",
            ),
        )
        event = listener.get_latest_event("BTC/USDT")
        assert event is not None
        assert event.event_id == "new-btc"


# ---------------------------------------------------------------------------
# Heartbeat / health tests
# ---------------------------------------------------------------------------


class TestHeartbeatHealth:
    """Tests for passive heartbeat-based health status."""

    @pytest.mark.asyncio
    async def test_healthy_within_threshold(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Health is HEALTHY when heartbeat is recent."""
        listener._last_heartbeat = time.monotonic()
        health = await listener.get_health_status()
        assert health.status == "HEALTHY"

    @pytest.mark.asyncio
    async def test_degraded_beyond_threshold(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Health is DEGRADED when heartbeat exceeds threshold."""
        listener._last_heartbeat = (
            time.monotonic() - 2.1
        )
        health = await listener.get_health_status()
        assert health.status == "DEGRADED"

    @pytest.mark.asyncio
    async def test_message_refreshes_heartbeat(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Processing a valid message refreshes the heartbeat."""
        listener._last_heartbeat = (
            time.monotonic() - 5.0
        )
        await listener._process_message(_make_event_payload())
        health = await listener.get_health_status()
        assert health.status == "HEALTHY"


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Tests for decode errors and degraded state transitions."""

    @pytest.mark.asyncio
    async def test_malformed_message_marks_degraded(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Malformed JSON message marks provider as DEGRADED."""
        await listener._process_message(b"not valid json")
        assert listener.status == "DEGRADED"

    @pytest.mark.asyncio
    async def test_recovery_after_error(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Valid message after error recovers to HEALTHY."""
        await listener._process_message(b"bad")
        assert listener.status == "DEGRADED"
        await listener._process_message(_make_event_payload())
        assert listener.status == "HEALTHY"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    """Tests for start/close lifecycle."""

    @pytest.mark.asyncio
    async def test_close_cancels_task(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """close() cancels the background listen task."""

        async def _hang_forever() -> None:
            """Simulate a long-running listener loop."""
            await asyncio.sleep(3600)

        real_task = asyncio.create_task(_hang_forever())
        listener._listen_task = real_task
        await listener.close()
        assert real_task.cancelled()
        assert listener._listen_task is None

    @pytest.mark.asyncio
    async def test_close_when_not_started(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """close() is safe when listener was never started."""
        await listener.close()
        assert listener._listen_task is None

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(
        self,
        listener: HydraStreamListener,
    ) -> None:
        """Second start() call is a no-op when already running."""
        mock_task = AsyncMock(spec=asyncio.Task)
        listener._listen_task = mock_task
        await listener.start()
        # Should not create a second task
        assert listener._listen_task is mock_task
