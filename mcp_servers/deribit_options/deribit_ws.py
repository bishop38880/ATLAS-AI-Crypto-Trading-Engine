"""Resilient async WebSocket client for Deribit V2 options data.

Section 26.3 Architecture — State Bridge producer.
Connects to wss://www.deribit.com/ws/api/v2, subscribes to ticker
and trades channels for BTC/ETH options, and continuously writes
parsed data into the DeribitStateCache.

Never called from MCP tool handlers directly.

Sentinel Invariants:
  - msgspec.json.decode for all WS message parsing
  - Loguru positional format logging
  - asyncio.CancelledError always re-raised
  - Exponential backoff with 60s cap on reconnect
  - No stdlib json, no requests, no os.getenv
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

import msgspec
import websockets
import websockets.exceptions
from loguru import logger

from .state_manager import DeribitStateCache

# Deribit WebSocket endpoints
_WS_URL_PROD = "wss://www.deribit.com/ws/api/v2"
_WS_URL_TEST = "wss://test.deribit.com/ws/api/v2"

# Reconnection backoff parameters
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_SECONDS = 60.0
_BACKOFF_MULTIPLIER = 2.0

# Heartbeat interval in seconds
_HEARTBEAT_INTERVAL = 10

# Instrument refresh interval in seconds (1 hour)
_INSTRUMENT_REFRESH_INTERVAL = 3600

# Supported coins for options
SUPPORTED_COINS = ("BTC", "ETH")


class DeribitWebSocketClient:
    """Resilient async WebSocket client for Deribit V2 options data.

    Section 26.3 Architecture — State Bridge producer.
    Writes continuously to DeribitStateCache. Never called from
    MCP tool handlers directly.
    """

    def __init__(
        self,
        cache: DeribitStateCache,
        *,
        use_testnet: bool = False,
    ) -> None:
        """Initialise the WebSocket client.

        Args:
            cache: Shared state cache to write live data into.
            use_testnet: If True, connect to Deribit testnet.
        """
        self._cache = cache
        self._ws_url = _WS_URL_TEST if use_testnet else _WS_URL_PROD
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._running = False
        self._msg_id_counter = 0
        self._backoff = _BACKOFF_BASE_SECONDS

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Main entry point — connect, subscribe, and loop with reconnect.

        This method runs indefinitely. Cancel the wrapping Task to stop.
        """
        self._running = True
        logger.info("Deribit WS client starting | url={}", self._ws_url)

        while self._running:
            try:
                await self._connect_and_stream()
            except asyncio.CancelledError:
                logger.info("Deribit WS client cancelled, shutting down")
                raise  # ALWAYS re-raise per Sentinel invariant
            except Exception as exc:
                self._cache.set_ws_connected(False)
                logger.warning(
                    "Deribit WS connection lost | error={} | backoff={}s",
                    exc,
                    self._backoff,
                )
                await asyncio.sleep(self._backoff)
                self._advance_backoff()

    def stop(self) -> None:
        """Signal the client to stop reconnecting."""
        self._running = False

    # ------------------------------------------------------------------
    # Connection and streaming
    # ------------------------------------------------------------------

    async def _connect_and_stream(self) -> None:
        """Establish WS connection, subscribe, and process messages."""
        async with websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            self._ws = ws
            self._cache.set_ws_connected(True)
            self._backoff = _BACKOFF_BASE_SECONDS
            logger.info("Deribit WS connected | url={}", self._ws_url)

            await self._setup_heartbeat(ws)
            await self._fetch_and_subscribe_instruments(ws)
            await self._message_loop(ws)

    async def _message_loop(
        self,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Process incoming WS messages indefinitely.

        Args:
            ws: Active WebSocket connection.
        """
        async for raw_msg in ws:
            try:
                msg = msgspec.json.decode(raw_msg)
                await self._dispatch_message(msg, ws)
            except msgspec.DecodeError:
                logger.warning(
                    "Failed to decode WS message | raw_len={}",
                    len(raw_msg) if isinstance(raw_msg, (str, bytes)) else 0,
                )

    async def _dispatch_message(
        self,
        msg: dict,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Route a decoded WS message to the appropriate handler.

        Args:
            msg: Decoded JSON message dict.
            ws: Active WebSocket connection (for heartbeat response).
        """
        method = msg.get("method", "")
        if method == "subscription":
            self._handle_subscription(msg)
        elif method == "heartbeat":
            await self._handle_heartbeat(msg, ws)

    # ------------------------------------------------------------------
    # Subscription data handlers
    # ------------------------------------------------------------------

    def _handle_subscription(self, msg: dict) -> None:
        """Route subscription notifications to the correct processor.

        Args:
            msg: Subscription notification message.
        """
        params = msg.get("params", {})
        channel = params.get("channel", "")
        data = params.get("data", {})

        if channel.startswith("ticker."):
            self._process_ticker(channel, data)
        elif channel.startswith("trades."):
            self._process_trades(data)

    def _process_ticker(self, channel: str, data: dict) -> None:
        """Process a ticker subscription update.

        Extracts instrument name and updates cache. Also updates
        the underlying index price from the ticker payload.

        Args:
            channel: Subscription channel name.
            data: Ticker data payload.
        """
        instrument = data.get("instrument_name", "")
        if not instrument:
            return

        self._cache.update_ticker(instrument, data)
        self._update_underlying_from_ticker(data)

    def _update_underlying_from_ticker(self, data: dict) -> None:
        """Extract and cache the underlying index price from ticker data.

        Args:
            data: Ticker data payload containing underlying_index.
        """
        instrument = data.get("instrument_name", "")
        underlying_price = data.get("underlying_price")
        if underlying_price and instrument:
            coin = instrument.split("-")[0]
            self._cache.update_underlying_price(
                coin,
                Decimal(str(underlying_price)),
            )

    def _process_trades(self, data: list | dict) -> None:
        """Process trade subscription updates.

        Deribit sends trades as a list of trade objects.

        Args:
            data: Trade data — either a list of trades or a single trade.
        """
        trades = data if isinstance(data, list) else [data]
        for trade in trades:
            self._cache.add_trade(trade)

    # ------------------------------------------------------------------
    # Heartbeat management
    # ------------------------------------------------------------------

    async def _setup_heartbeat(
        self,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Configure Deribit server-side heartbeat.

        Args:
            ws: Active WebSocket connection.
        """
        msg = self._build_rpc_msg(
            "public/set_heartbeat",
            {"interval": _HEARTBEAT_INTERVAL},
        )
        await ws.send(msgspec.json.encode(msg))
        logger.info(
            "Heartbeat configured | interval={}s",
            _HEARTBEAT_INTERVAL,
        )

    async def _handle_heartbeat(
        self,
        msg: dict,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Respond to Deribit heartbeat test requests.

        Args:
            msg: Heartbeat message from server.
            ws: Active WebSocket connection.
        """
        params = msg.get("params", {})
        if params.get("type") == "test_request":
            response = self._build_rpc_msg("public/test", {})
            await ws.send(msgspec.json.encode(response))

    # ------------------------------------------------------------------
    # Instrument discovery and subscription
    # ------------------------------------------------------------------

    async def _fetch_and_subscribe_instruments(
        self,
        ws: websockets.WebSocketClientProtocol,
    ) -> None:
        """Fetch instrument lists and subscribe to channels.

        For each supported coin, fetches the options instrument list
        from Deribit, stores it in cache, and subscribes to ticker
        and trades channels.

        Args:
            ws: Active WebSocket connection.
        """
        for coin in SUPPORTED_COINS:
            await self._fetch_instruments_for_coin(ws, coin)
            await self._subscribe_channels(ws, coin)

    async def _fetch_instruments_for_coin(
        self,
        ws: websockets.WebSocketClientProtocol,
        coin: str,
    ) -> None:
        """Fetch option instruments for a single coin via WS RPC.

        Args:
            ws: Active WebSocket connection.
            coin: Asset symbol (BTC or ETH).
        """
        msg = self._build_rpc_msg(
            "public/get_instruments",
            {"currency": coin, "kind": "option", "expired": False},
        )
        await ws.send(msgspec.json.encode(msg))

        raw_response = await asyncio.wait_for(ws.recv(), timeout=10.0)
        response = msgspec.json.decode(raw_response)
        instruments = response.get("result", [])
        self._cache.set_instruments(coin, instruments)

    async def _subscribe_channels(
        self,
        ws: websockets.WebSocketClientProtocol,
        coin: str,
    ) -> None:
        """Subscribe to ticker and trades channels for a coin.

        Subscribes to individual ticker channels for each instrument
        and the aggregated trades channel for the coin.

        Args:
            ws: Active WebSocket connection.
            coin: Asset symbol (BTC or ETH).
        """
        instruments = self._cache.get_instruments_for_coin(coin)
        channels = self._build_channel_list(coin, instruments)

        if not channels:
            logger.warning("No channels to subscribe | coin={}", coin)
            return

        await self._send_subscription_batch(ws, channels)

    def _build_channel_list(
        self,
        coin: str,
        instruments: list[dict],
    ) -> list[str]:
        """Build the list of subscription channels.

        Args:
            coin: Asset symbol (BTC or ETH).
            instruments: Instrument metadata list.

        Returns:
            List of Deribit channel strings.
        """
        channels: list[str] = []
        for inst in instruments:
            name = inst.get("instrument_name", "")
            if name:
                channels.append(f"ticker.{name}.100ms")

        channels.append(f"trades.option.{coin}.100ms")
        logger.info(
            "Built channel list | coin={} | count={}",
            coin,
            len(channels),
        )
        return channels

    async def _send_subscription_batch(
        self,
        ws: websockets.WebSocketClientProtocol,
        channels: list[str],
    ) -> None:
        """Send subscription requests in batches to avoid message size limits.

        Deribit accepts up to ~100 channels per subscription request.

        Args:
            ws: Active WebSocket connection.
            channels: Full list of channels to subscribe to.
        """
        batch_size = 100
        for i in range(0, len(channels), batch_size):
            batch = channels[i : i + batch_size]
            msg = self._build_rpc_msg(
                "public/subscribe",
                {"channels": batch},
            )
            await ws.send(msgspec.json.encode(msg))
            logger.info(
                "Subscribed to batch | offset={} | count={}",
                i,
                len(batch),
            )

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _build_rpc_msg(self, method: str, params: dict) -> dict:
        """Build a Deribit JSON-RPC 2.0 message.

        Args:
            method: RPC method name.
            params: Method parameters dict.

        Returns:
            Formatted JSON-RPC message dict.
        """
        self._msg_id_counter += 1
        return {
            "jsonrpc": "2.0",
            "id": self._msg_id_counter,
            "method": method,
            "params": params,
        }

    def _advance_backoff(self) -> None:
        """Increase backoff duration using exponential strategy."""
        self._backoff = min(
            self._backoff * _BACKOFF_MULTIPLIER,
            _BACKOFF_MAX_SECONDS,
        )
