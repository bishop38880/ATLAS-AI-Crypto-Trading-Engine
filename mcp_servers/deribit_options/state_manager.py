"""Thread-safe in-memory state cache for live Deribit options data.

Section 26.3 Architecture — State Bridge pattern.
The background WebSocket task writes; FastMCP tools read.
Dict/list single-key assignment is atomic in CPython, so no locks
are needed for this single-writer / multiple-reader pattern.

Sentinel Invariants:
  - msgspec for all JSON serialization
  - Loguru positional format logging
  - Decimal for financial values (notional, prices)
  - No stdlib json, no pandas, no os.getenv
"""

from __future__ import annotations

import time
from collections import deque
from decimal import Decimal

from loguru import logger


# Institutional block trade threshold in USD
_DEFAULT_BLOCK_THRESHOLD = Decimal("500000")

# Maximum rolling buffer sizes
_MAX_TRADES_BUFFER = 500
_MAX_BLOCK_TRADES_BUFFER = 50


class DeribitStateCache:
    """Thread-safe in-memory cache for live Deribit options data.

    Architecture: Section 26.3 — State Bridge pattern.
    Background WS task writes; FastMCP tools read.
    """

    def __init__(self) -> None:
        """Initialise empty state containers."""
        self._tickers: dict[str, dict] = {}
        self._trades: deque[dict] = deque(maxlen=_MAX_TRADES_BUFFER)
        self._block_trades: deque[dict] = deque(
            maxlen=_MAX_BLOCK_TRADES_BUFFER,
        )
        self._instruments: dict[str, list[dict]] = {}
        self._last_update_ts: float = 0.0
        self._ws_connected: bool = False
        self._underlying_prices: dict[str, Decimal] = {}

    # ------------------------------------------------------------------
    # Writer methods (called by WS background task only)
    # ------------------------------------------------------------------

    def update_ticker(self, instrument: str, data: dict) -> None:
        """Store or update a ticker snapshot for an instrument.

        Args:
            instrument: Deribit instrument name (e.g. BTC-30MAY25-100000-C).
            data: Raw ticker payload from Deribit WS.
        """
        self._tickers[instrument] = data
        self._last_update_ts = time.monotonic()

    def update_underlying_price(self, coin: str, price: Decimal) -> None:
        """Update the current underlying index price.

        Args:
            coin: Asset symbol (BTC or ETH).
            price: Current index price as Decimal.
        """
        self._underlying_prices[coin.upper()] = price

    def add_trade(self, trade: dict) -> None:
        """Add a trade event, filtering for block trades.

        Trades exceeding the institutional threshold are also
        stored in the block trades buffer.

        Args:
            trade: Raw trade payload from Deribit WS.
        """
        self._trades.appendleft(trade)
        self._last_update_ts = time.monotonic()
        self._check_block_trade(trade)

    def _check_block_trade(self, trade: dict) -> None:
        """Filter trade for institutional block threshold.

        Args:
            trade: Raw trade payload to evaluate.
        """
        notional = self._estimate_notional(trade)
        if notional >= _DEFAULT_BLOCK_THRESHOLD:
            trade_copy = dict(trade)
            trade_copy["notional_usd"] = str(notional)
            self._block_trades.appendleft(trade_copy)
            logger.info(
                "Block trade detected | instrument={} | notional_usd={} | direction={}",
                trade.get("instrument_name", "unknown"),
                notional,
                trade.get("direction", "unknown"),
            )

    def _estimate_notional(self, trade: dict) -> Decimal:
        """Estimate USD notional value for a trade.

        Uses the underlying index price and contract amount.
        Deribit options are quoted in the underlying asset.

        Args:
            trade: Raw trade payload containing amount and price.

        Returns:
            Estimated USD notional as Decimal.
        """
        amount = Decimal(str(trade.get("amount", 0)))
        price = Decimal(str(trade.get("price", 0)))
        instrument = trade.get("instrument_name", "")
        coin = instrument.split("-")[0] if "-" in instrument else ""
        underlying = self._underlying_prices.get(coin, Decimal("0"))
        return amount * underlying * price

    def set_instruments(self, coin: str, instruments: list[dict]) -> None:
        """Store the instrument list for a coin.

        Args:
            coin: Asset symbol (BTC or ETH).
            instruments: List of instrument metadata dicts from Deribit.
        """
        self._instruments[coin.upper()] = instruments
        logger.info(
            "Instruments updated | coin={} | count={}",
            coin.upper(),
            len(instruments),
        )

    def set_ws_connected(self, connected: bool) -> None:
        """Update WebSocket connection status.

        Args:
            connected: True if WS is connected, False otherwise.
        """
        self._ws_connected = connected

    # ------------------------------------------------------------------
    # Reader methods (called by MCP tool handlers)
    # ------------------------------------------------------------------

    def get_tickers_for_coin(self, coin: str) -> list[dict]:
        """Return all ticker snapshots for a given coin.

        Args:
            coin: Asset symbol (BTC or ETH).

        Returns:
            List of ticker dicts whose instrument name starts with the coin.
        """
        prefix = coin.upper() + "-"
        return [
            v for k, v in self._tickers.items()
            if k.startswith(prefix)
        ]

    def get_instruments_for_coin(self, coin: str) -> list[dict]:
        """Return instrument metadata list for a coin.

        Args:
            coin: Asset symbol (BTC or ETH).

        Returns:
            List of instrument metadata dicts.
        """
        return self._instruments.get(coin.upper(), [])

    def get_block_trades(
        self,
        coin: str,
        min_notional: Decimal | None = None,
    ) -> list[dict]:
        """Return filtered block trades for a coin.

        Args:
            coin: Asset symbol (BTC or ETH).
            min_notional: Minimum USD notional filter. Defaults to $500k.

        Returns:
            List of block trade dicts matching criteria.
        """
        threshold = min_notional or _DEFAULT_BLOCK_THRESHOLD
        prefix = coin.upper() + "-"
        return [
            t for t in self._block_trades
            if t.get("instrument_name", "").startswith(prefix)
            and Decimal(t.get("notional_usd", "0")) >= threshold
        ]

    def get_underlying_price(self, coin: str) -> Decimal:
        """Return the current underlying index price.

        Args:
            coin: Asset symbol (BTC or ETH).

        Returns:
            Current index price or Decimal(0) if unavailable.
        """
        return self._underlying_prices.get(coin.upper(), Decimal("0"))

    def get_ws_status(self) -> str:
        """Return human-readable WebSocket status string.

        Returns:
            'CONNECTED', 'DISCONNECTED', or 'INITIALIZING'.
        """
        if self._ws_connected:
            return "CONNECTED"
        if self._last_update_ts > 0:
            return "DISCONNECTED"
        return "INITIALIZING"

    def is_stale(self, max_age_seconds: float = 30.0) -> bool:
        """Check whether cached data is stale.

        Args:
            max_age_seconds: Maximum age before data is considered stale.

        Returns:
            True if last update was longer than max_age_seconds ago.
        """
        if self._last_update_ts == 0:
            return True
        elapsed = time.monotonic() - self._last_update_ts
        return elapsed > max_age_seconds
