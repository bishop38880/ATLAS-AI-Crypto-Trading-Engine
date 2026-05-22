"""Pyth Hermes provider test suite.

Tests:
    1. Decimal conversion correctly applies negative and positive exponents.
    2. SSE stream reconnects automatically on httpx.ReadError.
    3. Parsed payload writes to Redis mock successfully.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from atlas.providers.pyth.connector import PythHermesConnector
from atlas.providers.pyth.models import (
    PYTH_FEED_IDS,
    PythPriceUpdate,
    apply_pyth_exponent,
)
from atlas.shared.config import PolarisSettings


# ─── FIXTURES ────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> PolarisSettings:
    """Return default PolarisSettings for testing."""
    return PolarisSettings()


@pytest.fixture
def redis_client() -> AsyncMock:
    """Return a mock async Redis client."""
    r: AsyncMock = AsyncMock()
    r.setex = AsyncMock()
    return r


@pytest.fixture
def http_client() -> AsyncMock:
    """Return a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def connector(
    redis_client: AsyncMock,
    settings: PolarisSettings,
    http_client: AsyncMock,
) -> PythHermesConnector:
    """Return a PythHermesConnector wired with mocks."""
    return PythHermesConnector(
        redis_client=redis_client,
        settings=settings,
        http_client=http_client,
    )


# ─── TEST 1: DECIMAL CONVERSION ─────────────────────────────────────


class TestApplyPythExponent:
    """Verify Decimal conversion with various exponent values."""

    def test_negative_exponent(self) -> None:
        """price=6140993501000, expo=-8 → 61409.93501000."""
        result = apply_pyth_exponent("6140993501000", -8)
        assert result == Decimal("61409.93501000")
        assert isinstance(result, Decimal)

    def test_positive_exponent(self) -> None:
        """price=123, expo=2 → 12300."""
        result = apply_pyth_exponent("123", 2)
        assert result == Decimal("12300")
        assert isinstance(result, Decimal)

    def test_zero_exponent(self) -> None:
        """price=500, expo=0 → 500."""
        result = apply_pyth_exponent("500", 0)
        assert result == Decimal("500")

    def test_large_negative_exponent(self) -> None:
        """Typical Pyth crypto feed: expo=-8."""
        result = apply_pyth_exponent("4959503", -8)
        expected = Decimal("0.04959503")
        assert result == expected

    def test_confidence_interval(self) -> None:
        """conf=3287868567, expo=-8 → 32.87868567."""
        result = apply_pyth_exponent("3287868567", -8)
        assert result == Decimal("32.87868567")


class TestPythPriceUpdate:
    """Verify the frozen Pydantic model constraints."""

    def test_frozen_model(self) -> None:
        """PythPriceUpdate should be immutable."""
        update = PythPriceUpdate(
            price_id="abc123",
            price=Decimal("61409.93"),
            conf=Decimal("32.87"),
            publish_time=1714746101.0,
        )
        with pytest.raises(Exception):
            update.price = Decimal("99999")  # type: ignore[misc]

    def test_fields_are_decimal(self) -> None:
        """Price and conf must be Decimal instances."""
        update = PythPriceUpdate(
            price_id="abc123",
            price=Decimal("100.50"),
            conf=Decimal("0.05"),
            publish_time=1714746101.0,
        )
        assert isinstance(update.price, Decimal)
        assert isinstance(update.conf, Decimal)


# ─── TEST 2: SSE RECONNECTION ───────────────────────────────────────


class TestSSEReconnection:
    """Verify exponential backoff reconnection on stream errors."""

    @pytest.mark.asyncio
    async def test_reconnects_on_read_error(
        self,
        connector: PythHermesConnector,
    ) -> None:
        """Connector should catch ReadError and retry."""
        call_count = 0

        async def mock_consume_stream() -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.ReadError("connection reset")
            raise asyncio.CancelledError

        with patch.object(
            connector, "_consume_stream", side_effect=mock_consume_stream
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(asyncio.CancelledError):
                    await connector._stream_loop()

        assert call_count == 3
        assert mock_sleep.call_count == 2
        # Verify exponential backoff: 1.0, then 2.0
        mock_sleep.assert_any_call(1.0)
        mock_sleep.assert_any_call(2.0)

    @pytest.mark.asyncio
    async def test_backoff_caps_at_max(
        self,
        connector: PythHermesConnector,
    ) -> None:
        """Backoff should not exceed pyth_reconnect_max_seconds."""
        call_count = 0
        max_backoff = connector._settings.pyth_reconnect_max_seconds

        async def mock_consume() -> None:
            nonlocal call_count
            call_count += 1
            if call_count <= 10:
                raise httpx.ReadError("timeout")
            raise asyncio.CancelledError

        with patch.object(
            connector, "_consume_stream", side_effect=mock_consume
        ):
            with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                with pytest.raises(asyncio.CancelledError):
                    await connector._stream_loop()

        # Last backoff call should be capped
        last_sleep = mock_sleep.call_args_list[-1][0][0]
        assert last_sleep <= max_backoff


# ─── TEST 3: REDIS WRITE ────────────────────────────────────────────


class TestRedisWrite:
    """Verify parsed payloads are written to Redis correctly."""

    @pytest.mark.asyncio
    async def test_process_sse_writes_to_redis(
        self,
        connector: PythHermesConnector,
        redis_client: AsyncMock,
    ) -> None:
        """A valid SSE payload should result in a Redis setex call."""
        btc_feed_id = PYTH_FEED_IDS["BTC"]
        raw_data = (
            '{{"parsed": [{{"id": "{feed_id}", '
            '"price": {{"price": "6140993501000", '
            '"conf": "3287868567", "expo": -8, '
            '"publish_time": 1714746101}}}}]}}'
        ).format(feed_id=btc_feed_id)

        await connector._process_sse_data(raw_data)

        redis_client.setex.assert_called_once()
        call_args = redis_client.setex.call_args
        key = call_args[0][0]
        ttl = call_args[0][1]
        assert key == "atlas:price:BTC"
        assert ttl == connector._settings.pyth_price_ttl_seconds

    @pytest.mark.asyncio
    async def test_unknown_feed_id_skipped(
        self,
        connector: PythHermesConnector,
        redis_client: AsyncMock,
    ) -> None:
        """An unrecognised feed ID should not write to Redis."""
        raw_data = (
            '{"parsed": [{"id": "unknown_feed_id_abc123", '
            '"price": {"price": "100", "conf": "1", '
            '"expo": -2, "publish_time": 1714746101}}]}'
        )

        await connector._process_sse_data(raw_data)
        redis_client.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_malformed_payload_does_not_crash(
        self,
        connector: PythHermesConnector,
        redis_client: AsyncMock,
    ) -> None:
        """Malformed JSON should log error, not raise."""
        await connector._process_sse_data("not valid json{{{")
        redis_client.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_connector_marks_healthy_on_success(
        self,
        connector: PythHermesConnector,
        redis_client: AsyncMock,
    ) -> None:
        """Connector should be HEALTHY after processing a valid update."""
        connector._status = "DEGRADED"
        btc_feed_id = PYTH_FEED_IDS["BTC"]
        raw_data = (
            '{{"parsed": [{{"id": "{feed_id}", '
            '"price": {{"price": "6140993501000", '
            '"conf": "3287868567", "expo": -8, '
            '"publish_time": 1714746101}}}}]}}'
        ).format(feed_id=btc_feed_id)

        await connector._process_sse_data(raw_data)
        assert connector.status == "HEALTHY"


# ─── TEST 4: LIFECYCLE ──────────────────────────────────────────────


class TestLifecycle:
    """Verify start/close lifecycle."""

    @pytest.mark.asyncio
    async def test_close_cancels_task(
        self,
        connector: PythHermesConnector,
    ) -> None:
        """close() should cancel the background task."""

        async def _hang_forever() -> None:
            await asyncio.sleep(3600)

        task = asyncio.create_task(_hang_forever())
        connector._stream_task = task

        await connector.close()

        assert task.cancelled()
        assert connector._stream_task is None

    @pytest.mark.asyncio
    async def test_health_check(
        self,
        connector: PythHermesConnector,
    ) -> None:
        """health_check returns True when HEALTHY."""
        assert await connector.health_check() is True
        connector._status = "DEGRADED"
        assert await connector.health_check() is False
