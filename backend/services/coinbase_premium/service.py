"""
Coinbase Premium % Service for POLARIS.

Maintains Coinbase Exchange and Binance WebSocket feeds, computes premium %,
and stores rolling windows in Redis for the confluence scoring engine.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import msgspec
import websockets
import websockets.exceptions
from loguru import logger
from redis.asyncio import Redis

from backend.services.coinbase_premium.reconnect import ReconnectManager
from backend.services.coinbase_premium.signals import REDIS_PREFIX, compute_signals

COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
BINANCE_WS_URL = (
    "wss://stream.binance.com:9443/ws/btcusdt@miniTicker/ethusdt@miniTicker"
)

HISTORY_MAX_AGE = 3700
HISTORY_MAX_LEN = 720
SIGNALS_TTL = 30
CURRENT_TTL = 10

SYMBOLS: dict[str, dict[str, str]] = {
    "BTC": {"coinbase": "BTC-USD", "binance_key": "btcusdt"},
    "ETH": {"coinbase": "ETH-USD", "binance_key": "ethusdt"},
}


class CoinbasePremiumService:
    """Live Coinbase Premium % for BTC and ETH."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []
        self._coinbase_prices: dict[str, float] = {}
        self._binance_prices: dict[str, float] = {}
        self._last_computed: dict[str, float] = {}
        self._cb_connected = False
        self._bn_connected = False
        self._tick_count = 0

    async def start(self) -> None:
        """Start WebSocket background tasks."""
        self._running = True
        logger.info("coinbase_premium_service_starting")

        self._tasks = [
            asyncio.create_task(self._run_coinbase(), name="cb_premium_ws"),
            asyncio.create_task(self._run_binance(), name="bn_premium_ws"),
            asyncio.create_task(self._run_health_reporter(), name="premium_health"),
        ]

        await self._redis.set(f"{REDIS_PREFIX}:service:status", "starting", ex=30)
        logger.info("coinbase_premium_ws_tasks_launched")

    async def stop(self) -> None:
        """Gracefully stop all tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._redis.set(f"{REDIS_PREFIX}:service:status", "stopped")
        logger.info("coinbase_premium_service_stopped")

    async def _run_coinbase(self) -> None:
        mgr = ReconnectManager("coinbase_ws", base_delay=1.0, max_delay=30.0)
        async for attempt in mgr.attempts():
            if not self._running:
                break
            try:
                logger.info(
                    "coinbase_premium_coinbase_connecting | attempt={}",
                    attempt,
                )
                async with websockets.connect(
                    COINBASE_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    subscribe_msg = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD", "ETH-USD"],
                        "channels": ["ticker"],
                    }
                    await ws.send(msgspec.json.encode(subscribe_msg))
                    self._cb_connected = True
                    logger.info("coinbase_premium_coinbase_connected")
                    mgr.reset()

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        await self._handle_coinbase_message(raw_msg)

            except asyncio.CancelledError:
                self._cb_connected = False
                raise
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as exc:
                self._cb_connected = False
                logger.warning(
                    "coinbase_premium_coinbase_disconnected | err={}",
                    str(exc),
                )

    async def _handle_coinbase_message(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                msg = msgspec.json.decode(raw)
            else:
                msg = msgspec.json.decode(raw.encode())
        except Exception:
            return

        if msg.get("type") != "ticker":
            return

        product = msg.get("product_id", "")
        price_str = msg.get("price")
        if not product or not price_str:
            return

        symbol = str(product).split("-")[0]
        if symbol not in SYMBOLS:
            return

        try:
            price = float(price_str)
        except (ValueError, TypeError):
            return

        self._coinbase_prices[symbol] = price
        await self._compute_and_store(symbol)

    async def _run_binance(self) -> None:
        mgr = ReconnectManager("binance_ws", base_delay=1.0, max_delay=30.0)
        async for attempt in mgr.attempts():
            if not self._running:
                break
            try:
                logger.info(
                    "coinbase_premium_binance_connecting | attempt={}",
                    attempt,
                )
                async with websockets.connect(
                    BINANCE_WS_URL,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self._bn_connected = True
                    logger.info("coinbase_premium_binance_connected")
                    mgr.reset()

                    async for raw_msg in ws:
                        if not self._running:
                            break
                        await self._handle_binance_message(raw_msg)

            except asyncio.CancelledError:
                self._bn_connected = False
                raise
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.WebSocketException,
                OSError,
            ) as exc:
                self._bn_connected = False
                logger.warning(
                    "coinbase_premium_binance_disconnected | err={}",
                    str(exc),
                )

    async def _handle_binance_message(self, raw: str | bytes) -> None:
        try:
            if isinstance(raw, bytes):
                msg = msgspec.json.decode(raw)
            else:
                msg = msgspec.json.decode(raw.encode())
        except Exception:
            return

        if "data" in msg:
            msg = msg["data"]

        if msg.get("e", "") != "24hrMiniTicker":
            return

        ticker_symbol = str(msg.get("s", "")).upper()
        close_str = msg.get("c")
        if not close_str:
            return

        symbol: str | None = None
        for sym, config in SYMBOLS.items():
            if ticker_symbol == config["binance_key"].upper():
                symbol = sym
                break

        if symbol is None:
            return

        try:
            price = float(close_str)
        except (ValueError, TypeError):
            return

        self._binance_prices[symbol] = price
        await self._compute_and_store(symbol)

    async def _compute_and_store(self, symbol: str) -> None:
        """Compute premium % and persist to Redis (throttled to 1 Hz per symbol)."""
        now = time.time()
        if now - self._last_computed.get(symbol, 0.0) < 1.0:
            return

        cb_price = self._coinbase_prices.get(symbol)
        bn_price = self._binance_prices.get(symbol)
        if cb_price is None or bn_price is None or bn_price == 0:
            return

        premium_pct = (cb_price - bn_price) / bn_price * 100.0
        self._last_computed[symbol] = now
        self._tick_count += 1
        symbol_lower = symbol.lower()

        await self._redis.set(
            f"{REDIS_PREFIX}:{symbol_lower}:current",
            str(round(premium_pct, 4)),
            ex=CURRENT_TTL,
        )
        await self._redis.set(
            f"{REDIS_PREFIX}:{symbol_lower}:cb_price",
            str(cb_price),
            ex=CURRENT_TTL,
        )
        await self._redis.set(
            f"{REDIS_PREFIX}:{symbol_lower}:bn_price",
            str(bn_price),
            ex=CURRENT_TTL,
        )

        history_key = f"{REDIS_PREFIX}:{symbol_lower}:history"
        entry = f"{now}:{premium_pct:.4f}"
        await self._redis.zadd(history_key, {entry: now})

        cutoff = now - HISTORY_MAX_AGE
        await self._redis.zremrangebyscore(history_key, "-inf", cutoff)

        count = await self._redis.zcard(history_key)
        if count > HISTORY_MAX_LEN:
            await self._redis.zpopmin(history_key, count - HISTORY_MAX_LEN)

        signals_key = f"{symbol}_signals"
        if now - self._last_computed.get(signals_key, 0.0) >= 5.0:
            await self._refresh_signals(symbol, symbol_lower, history_key, premium_pct)
            self._last_computed[signals_key] = now

        await self._redis.set(
            f"{REDIS_PREFIX}:service:last_tick",
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ex=15,
        )

    async def _refresh_signals(
        self,
        symbol: str,
        symbol_lower: str,
        history_key: str,
        current_premium: float,
    ) -> None:
        raw_entries = await self._redis.zrangebyscore(
            history_key,
            time.time() - 3600,
            "+inf",
            withscores=True,
        )

        history: list[tuple[float, float]] = []
        for entry, _score in raw_entries:
            try:
                text = entry.decode() if isinstance(entry, bytes) else str(entry)
                parts = text.split(":")
                ts = float(parts[0])
                prem = float(parts[1])
                history.append((ts, prem))
            except (ValueError, IndexError):
                continue

        signals = compute_signals(history, current_premium)
        signals["coinbase_price"] = self._coinbase_prices.get(symbol)
        signals["binance_price"] = self._binance_prices.get(symbol)
        signals["cb_connected"] = self._cb_connected
        signals["bn_connected"] = self._bn_connected

        await self._redis.set(
            f"{REDIS_PREFIX}:{symbol_lower}:signals",
            msgspec.json.encode(signals),
            ex=SIGNALS_TTL,
        )

    async def _run_health_reporter(self) -> None:
        while self._running:
            try:
                if self._cb_connected and self._bn_connected:
                    status = "running"
                elif self._cb_connected or self._bn_connected:
                    status = "degraded"
                else:
                    status = "disconnected"

                await self._redis.set(
                    f"{REDIS_PREFIX}:service:status",
                    status,
                    ex=30,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(
                    "coinbase_premium_health_reporter_error | err={}",
                    str(exc),
                )

            await asyncio.sleep(10)

    async def get_signals(self, symbol: str = "BTC") -> dict[str, Any] | None:
        raw = await self._redis.get(f"{REDIS_PREFIX}:{symbol.lower()}:signals")
        if raw is None:
            return None
        try:
            if isinstance(raw, bytes):
                return msgspec.json.decode(raw)
            return msgspec.json.decode(str(raw).encode())
        except Exception:
            return None

    async def get_current_premium(self, symbol: str = "BTC") -> float | None:
        raw = await self._redis.get(f"{REDIS_PREFIX}:{symbol.lower()}:current")
        if raw is None:
            return None
        try:
            text = raw.decode() if isinstance(raw, bytes) else str(raw)
            return float(text)
        except (ValueError, TypeError):
            return None

    def is_healthy(self) -> bool:
        return self._cb_connected or self._bn_connected

    def status(self) -> dict[str, object]:
        return {
            "coinbase_connected": self._cb_connected,
            "binance_connected": self._bn_connected,
            "tick_count": self._tick_count,
        }
