"""Full-featured Bitget V2 perpetual futures REST client.

Scope: read account/positions/orders/market, place orders and plans,
cancel and modify plans.  Emergency halt operations live in the
separate kill_switch module and are intentionally duplicated there.

Signing delegates to ``prometheus.shared.bitget_signing`` — the ONE
shared module between kill switch and execution client.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import ROUND_DOWN, ROUND_UP, Decimal
from typing import TYPE_CHECKING, Any

import httpx
import msgspec
import pybreaker
import redis.asyncio as redis_async
from loguru import logger

from prometheus.execution.models import (
    BitgetAccountEquity,
    BitgetContractSpec,
    BitgetEnvelope,
    BitgetOpenOrder,
    BitgetPlanOrder,
    BitgetPosition,
    BitgetTicker,
    InsufficientSizeError,
    ModifyPlanOrderRequest,
    PlaceOrderRequest,
    PlaceOrderResult,
    PlacePlanOrderRequest,
    PlaceTpslOrderRequest,
)
from prometheus.shared.bitget_signing import build_signed_headers

if TYPE_CHECKING:
    from prometheus.settings import PrometheusSettings
    from prometheus.execution.circuit_breaker import CircuitBreakerFactory

# Bitget rate-limit error code
_RATE_LIMIT_CODE = "40014"
_SUCCESS_CODE = "00000"
_RATE_LIMIT_BREAKER_OPEN_S = 30
_HTTP_TIMEOUT_S = 10.0
_REDIS_TIMEOUT_S = 5.0


def _bool_setting(settings: Any, name: str, default: bool) -> bool:
    value = getattr(settings, name, default)
    return value if isinstance(value, bool) else default


def _paper_mode_enabled(settings: Any) -> bool:
    explicit_dry_run = getattr(settings, "paper_trade_dry_run", None)
    if isinstance(explicit_dry_run, bool):
        return explicit_dry_run or not _bool_setting(
            settings,
            "bitget_demo_write_enabled",
            False,
        )

    legacy_enabled = getattr(settings, "paper_trade_enabled", None)
    if isinstance(legacy_enabled, bool):
        return legacy_enabled

    return True


class _BreakerFailure(Exception):
    """Raised inside cb.call() to signal a failure to pybreaker."""


def _raise_for_breaker() -> None:
    """Sync callable that always raises — used to register breaker failures."""
    raise _BreakerFailure("breaker failure signal")



class BitgetExecutionClient:
    """Full-featured Bitget V2 perpetual futures client.

    Scope: read account/positions/orders/market, place orders and plans,
    cancel and modify plans.  Emergency halt operations live in the
    separate kill_switch module and are intentionally duplicated there.
    """

    def __init__(
        self,
        settings: PrometheusSettings,
        http_client: httpx.AsyncClient,
        circuit_breaker_factory: CircuitBreakerFactory,
        redis_client: redis_async.Redis | None = None,  # type: ignore[type-arg]
    ) -> None:
        self._settings = settings
        self._http = http_client
        self._redis = redis_client
        self._base_url = settings.bitget_base_url
        self._api_key = settings.bitget_api_key.get_secret_value()
        self._api_secret = settings.bitget_secret_key.get_secret_value()
        self._passphrase = settings.bitget_api_passphrase.get_secret_value()
        self._paper = _paper_mode_enabled(settings)
        self._read_breaker = circuit_breaker_factory.create(
            "bitget_read", fail_max=5, reset_timeout=30,
        )
        self._write_breaker = circuit_breaker_factory.create(
            "bitget_write", fail_max=3, reset_timeout=30,
        )

    # ── Read endpoints ───────────────────────────────────────────────

    async def get_account_equity(
        self, margin_coin: str = "USDT",
    ) -> BitgetAccountEquity:
        """Fetch single-asset account detail (equity, available, locked)."""
        path = (
            "/api/v2/mix/account/account"
            f"?symbol=BTCUSDT&productType=USDT-FUTURES&marginCoin={margin_coin}"
        )
        data = await self._signed_get(path)
        if data is None:
            return self._empty_equity(margin_coin)
        return _parse_account_equity(data, margin_coin)

    async def get_position(
        self, symbol: str, product_type: str = "USDT-FUTURES",
    ) -> BitgetPosition | None:
        """Fetch single position for a symbol. None if no position."""
        path = (
            "/api/v2/mix/position/single-position"
            f"?symbol={symbol}&productType={product_type}&marginCoin=USDT"
        )
        data = await self._signed_get(path)
        if data is None:
            return None
        positions = _parse_positions(data)
        active = [p for p in positions if p.total > 0]
        return active[0] if active else None

    async def list_open_positions(
        self, product_type: str = "USDT-FUTURES",
    ) -> list[BitgetPosition]:
        """Fetch all open positions for a product type."""
        path = (
            "/api/v2/mix/position/all-position"
            f"?productType={product_type}&marginCoin=USDT"
        )
        data = await self._signed_get(path)
        if data is None:
            return []
        positions = _parse_positions(data)
        return [p for p in positions if p.total > 0]

    async def list_open_orders(
        self, product_type: str = "USDT-FUTURES",
    ) -> list[BitgetOpenOrder]:
        """Fetch open (pending) orders."""
        path = (
            "/api/v2/mix/order/orders-pending"
            f"?productType={product_type}"
        )
        data = await self._signed_get(path)
        if data is None:
            return []
        return _parse_open_orders(data)

    async def list_open_plan_orders(
        self, product_type: str = "USDT-FUTURES",
    ) -> list[BitgetPlanOrder]:
        """Fetch open plan/trigger orders."""
        path = (
            "/api/v2/mix/order/orders-plan-pending"
            f"?productType={product_type}"
        )
        data = await self._signed_get(path)
        if data is None:
            return []
        return _parse_plan_orders(data)

    async def get_ticker(self, symbol: str) -> BitgetTicker:
        """Fetch latest ticker for a symbol."""
        path = (
            "/api/v2/mix/market/ticker"
            f"?symbol={symbol}&productType=USDT-FUTURES"
        )
        data = await self._signed_get(path)
        if data is None:
            return _empty_ticker(symbol)
        return _parse_ticker(data, symbol)

    async def get_contract_spec(self, symbol: str) -> BitgetContractSpec:
        """Fetch contract specification (lot size, steps, fees)."""
        path = (
            "/api/v2/mix/market/contracts"
            f"?symbol={symbol}&productType=USDT-FUTURES"
        )
        data = await self._signed_get(path)
        if data is None:
            return _empty_contract_spec(symbol)
        return _parse_contract_spec(data, symbol)

    # ── Write endpoints ──────────────────────────────────────────────

    async def place_order(
        self, req: PlaceOrderRequest,
    ) -> PlaceOrderResult:
        """Place a market or limit order."""
        if self._paper:
            return _paper_result(req.client_oid)
        path = "/api/v2/mix/order/place-order"
        return await self._signed_post(path, req)

    async def place_plan_order(
        self, req: PlacePlanOrderRequest,
    ) -> PlaceOrderResult:
        """Place a standalone price-triggered order (stop ladder)."""
        if self._paper:
            return _paper_result(req.client_oid)
        path = "/api/v2/mix/order/place-plan-order"
        return await self._signed_post(path, req)

    async def place_tpsl_order(
        self, req: PlaceTpslOrderRequest,
    ) -> PlaceOrderResult:
        """Place a position-attached TP/SL plan."""
        if self._paper:
            return _paper_result(req.client_oid)
        path = "/api/v2/mix/order/place-tpsl-order"
        return await self._signed_post(path, req)

    async def cancel_plan_order(
        self,
        order_id: str,
        product_type: str = "USDT-FUTURES",
    ) -> bool:
        """Cancel a specific plan order. Returns success bool."""
        if self._paper:
            return True
        payload = {"orderId": order_id, "productType": product_type}
        body = msgspec.json.encode(payload)
        path = "/api/v2/mix/order/cancel-plan-order"
        result = await self._do_post(path, body)
        return result.success

    async def modify_plan_order(
        self, req: ModifyPlanOrderRequest,
    ) -> PlaceOrderResult:
        """Modify trigger price or size of an existing plan order."""
        if self._paper:
            return _paper_result(None)
        path = "/api/v2/mix/order/modify-plan-order"
        return await self._signed_post(path, req)

    # ── Helpers ───────────────────────────────────────────────────────

    async def usd_notional_to_base_coin_size(
        self,
        symbol: str,
        notional_usd: Decimal,
        reference_price: Decimal | None = None,
    ) -> Decimal:
        """Convert USD notional to base-coin size, rounded to contract spec.

        size_base = (notional_usd / price).quantize(size_multiplier).
        If reference_price is None, fetches ticker.
        Respects min_trade_num and size_multiplier from contract spec.
        Rounds UP to min_trade_num if result is below minimum.

        Raises:
            InsufficientSizeError: If notional is < 10% of minimum size.
        """
        spec = await self.get_contract_spec(symbol)
        price = reference_price
        if price is None:
            ticker = await self.get_ticker(symbol)
            price = ticker.last_price
        if price <= 0:
            raise ValueError(f"Invalid price {price} for {symbol}")
        raw_size = notional_usd / price
        rounded = _quantize_to_step(raw_size, spec.size_multiplier)
        min_notional = spec.min_trade_num * price
        if notional_usd < min_notional * Decimal("0.1"):
            raise InsufficientSizeError(
                f"${notional_usd} is <10% of minimum "
                f"${min_notional} for {symbol}"
            )
        if rounded < spec.min_trade_num:
            rounded = spec.min_trade_num
        return rounded

    # ── Internal transport ────────────────────────────────────────────

    def _sign_headers(
        self, method: str, path: str, body: bytes = b"",
    ) -> dict[str, str]:
        """Delegates to prometheus/shared/bitget_signing.sign_request()."""
        return build_signed_headers(
            self._api_key, self._api_secret,
            self._passphrase, method, path, body,
        )

    async def _signed_get(self, path: str) -> msgspec.Raw | None:
        """Execute a signed GET with circuit breaker and rate limits."""
        if self._read_breaker.current_state == pybreaker.STATE_OPEN:
            logger.warning("read_breaker_open | path={}", path)
            return None
        result = await self._do_get(path)
        try:
            self._read_breaker.call(lambda: result)
        except pybreaker.CircuitBreakerError:
            logger.debug("read_breaker_tripped_during_record | path={}", path)
        return result

    async def _do_get(self, path: str) -> msgspec.Raw | None:
        """Perform the actual signed GET request."""
        headers = self._sign_headers("GET", path)
        url = f"{self._base_url}{path}"
        resp = await self._http.get(
            url, headers=headers, timeout=_HTTP_TIMEOUT_S,
        )
        await self._process_rate_limit_headers(resp)
        if resp.status_code == 429:
            await self._handle_rate_limit()
            return None
        try:
            envelope = msgspec.json.decode(resp.content, type=BitgetEnvelope)
        except msgspec.DecodeError as exc:
            logger.error("get_envelope_decode_failed | path={} | error={}", path, str(exc))
            return None
        if envelope.code == _RATE_LIMIT_CODE:
            await self._handle_rate_limit()
            return None
        if envelope.code != _SUCCESS_CODE:
            logger.error(
                "bitget_api_error | code={} | msg={} | path={}",
                envelope.code, envelope.msg, path,
            )
            return None
        return envelope.data

    async def _signed_post(
        self, path: str, req: object,
    ) -> PlaceOrderResult:
        """Execute a signed POST with circuit breaker."""
        payload_dict = req.model_dump(mode="json")  # type: ignore[union-attr]
        body = msgspec.json.encode(payload_dict)
        if self._write_breaker.current_state == pybreaker.STATE_OPEN:
            logger.warning("write_breaker_open | path={}", path)
            return PlaceOrderResult(
                success=False,
                errors=["circuit breaker open — DEGRADED"],
            )
        try:
            result = await self._do_post(path, body)
            if result.success:
                try:
                    self._write_breaker.call(lambda: result)
                except pybreaker.CircuitBreakerError:
                    logger.debug("write_breaker_tripped_on_success | path={}", path)
            else:
                try:
                    self._write_breaker.call(_raise_for_breaker)
                except (pybreaker.CircuitBreakerError, _BreakerFailure):
                    logger.debug("write_breaker_failure_recorded | path={}", path)
            return result
        except Exception as exc:
            try:
                self._write_breaker.call(_raise_for_breaker)
            except (pybreaker.CircuitBreakerError, _BreakerFailure):
                logger.debug("write_breaker_failure_on_exc | path={}", path)
            logger.error("write_request_failed | path={} | error={}", path, str(exc))
            return PlaceOrderResult(
                success=False,
                errors=[f"request error: {exc} — DEGRADED"],
            )

    async def _do_post(self, path: str, body: bytes) -> PlaceOrderResult:
        """Perform the actual signed POST request."""
        headers = self._sign_headers("POST", path, body)
        url = f"{self._base_url}{path}"
        resp = await self._http.post(
            url, content=body, headers=headers, timeout=_HTTP_TIMEOUT_S,
        )
        await self._process_rate_limit_headers(resp)
        if resp.status_code == 429:
            await self._handle_rate_limit()
            return PlaceOrderResult(
                success=False, errors=["rate limit 429 — DEGRADED"],
            )
        try:
            envelope = msgspec.json.decode(
                resp.content, type=BitgetEnvelope,
            )
        except msgspec.DecodeError as exc:
            logger.error("envelope_decode_failed | error={}", str(exc))
            return PlaceOrderResult(
                success=False,
                errors=[f"response parse error: {exc}"],
            )
        if envelope.code == _RATE_LIMIT_CODE:
            await self._handle_rate_limit()
            return PlaceOrderResult(
                success=False, errors=["rate limit — DEGRADED"],
            )
        if envelope.code != _SUCCESS_CODE:
            return PlaceOrderResult(
                success=False,
                errors=[f"Bitget error {envelope.code}: {envelope.msg}"],
            )
        return _parse_order_result(envelope.data)

    # ── Rate limit accounting (Task 3) ────────────────────────────────

    async def _process_rate_limit_headers(
        self, resp: httpx.Response,
    ) -> None:
        """Extract rate limit headers, publish to Redis, warn if low."""
        remaining = resp.headers.get("X-BAPI-LIMIT-STATUS")
        limit = resp.headers.get("X-BAPI-LIMIT")
        if remaining is None or limit is None:
            return
        try:
            remaining_int = int(remaining)
            limit_int = int(limit)
        except ValueError:
            return
        threshold = int(limit_int * 0.2)
        if remaining_int < threshold:
            logger.warning(
                "bitget_rate_limit_low | remaining={} | limit={}",
                remaining_int,
                limit_int,
            )
        if self._redis is not None:
            await self._publish_rate_limit(remaining_int, limit_int)

    async def _publish_rate_limit(
        self, remaining: int, limit: int,
    ) -> None:
        """Publish remaining rate budget to Redis with 60s TTL."""
        try:
            payload = msgspec.json.encode(
                {"remaining": remaining, "limit": limit},
            )
            key = f"bitget:rate_limit:{self._api_key[:8]}"
            await asyncio.wait_for(
                self._redis.set(key, payload, ex=60),  # type: ignore[union-attr]
                timeout=_REDIS_TIMEOUT_S,
            )
        except Exception as exc:
            logger.debug("rate_limit_redis_publish_failed | error={}", str(exc))

    async def _handle_rate_limit(self) -> None:
        """Open breakers for 30s on rate limit hit."""
        logger.warning(
            "bitget_rate_limited | open_s={}",
            _RATE_LIMIT_BREAKER_OPEN_S,
        )
        self._read_breaker.open()
        self._write_breaker.open()

    # ── Paper trading helpers ─────────────────────────────────────────

    @staticmethod
    def _empty_equity(margin_coin: str) -> BitgetAccountEquity:
        """Return zero-equity response for degraded/paper mode."""
        return BitgetAccountEquity(
            margin_coin=margin_coin,
            available=Decimal("0"),
            equity=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            locked=Decimal("0"),
        )


# ── Pure parsing helpers (outside class, ≤40 lines each) ─────────────


def _paper_result(client_oid: str | None) -> PlaceOrderResult:
    """Generate a realistic paper-trading write response."""
    return PlaceOrderResult(
        success=True,
        order_id=f"PAPER-{uuid.uuid4().hex[:12]}",
        client_oid=client_oid,
        paper_trading=True,
    )


def _parse_account_equity(
    data: msgspec.Raw, margin_coin: str,
) -> BitgetAccountEquity:
    """Parse account endpoint data into typed model."""
    raw = msgspec.json.decode(data, type=list[dict[str, str]])
    if not raw:
        return BitgetExecutionClient._empty_equity(margin_coin)
    item = raw[0]
    return BitgetAccountEquity(
        margin_coin=item.get("marginCoin", margin_coin),
        available=Decimal(item.get("available", "0")),
        equity=Decimal(item.get("accountEquity", "0")),
        unrealized_pnl=Decimal(item.get("unrealizedPL", "0")),
        locked=Decimal(item.get("locked", "0")),
    )


def _parse_positions(data: msgspec.Raw) -> list[BitgetPosition]:
    """Parse position endpoint data into typed models."""
    raw = msgspec.json.decode(data, type=list[dict[str, str]])
    result: list[BitgetPosition] = []
    for item in raw:
        pos = BitgetPosition(
            symbol=item.get("symbol", ""),
            product_type=item.get("productType", "USDT-FUTURES"),
            margin_mode=item.get("marginMode", "isolated"),  # type: ignore[arg-type]
            margin_coin=item.get("marginCoin", "USDT"),
            side=item.get("holdSide", "long"),  # type: ignore[arg-type]
            total=Decimal(item.get("total", "0")),
            available=Decimal(item.get("available", "0")),
            leverage=Decimal(item.get("leverage", "1")),
            average_open_price=Decimal(item.get("averageOpenPrice", "0")),
            mark_price=Decimal(item.get("markPrice", "0")),
            unrealized_pnl=Decimal(item.get("unrealizedPL", "0")),
            margin_size=Decimal(item.get("marginSize", "0")),
            liquidation_price=_opt_decimal(item.get("liquidationPrice")),
            created_at=_parse_ms_timestamp(item.get("cTime", "0")),
        )
        result.append(pos)
    return result


def _parse_open_orders(data: msgspec.Raw) -> list[BitgetOpenOrder]:
    """Parse pending orders into typed models."""
    wrapper = msgspec.json.decode(data, type=dict)
    items = wrapper.get("entrustedList", wrapper.get("orderList", []))
    if not isinstance(items, list):
        items = []
    result: list[BitgetOpenOrder] = []
    for item in items:
        order = BitgetOpenOrder(
            order_id=str(item.get("orderId", "")),
            client_oid=str(item.get("clientOid", "")),
            symbol=str(item.get("symbol", "")),
            side=str(item.get("side", "")),
            order_type=str(item.get("orderType", "")),
            price=Decimal(str(item.get("price", "0"))),
            size=Decimal(str(item.get("size", "0"))),
            status=str(item.get("status", "")),
            created_at=_parse_ms_timestamp(str(item.get("cTime", "0"))),
        )
        result.append(order)
    return result


def _parse_plan_orders(data: msgspec.Raw) -> list[BitgetPlanOrder]:
    """Parse pending plan orders into typed models."""
    wrapper = msgspec.json.decode(data, type=dict)
    items = wrapper.get("entrustedList", wrapper.get("orderList", []))
    if not isinstance(items, list):
        items = []
    result: list[BitgetPlanOrder] = []
    for item in items:
        plan = BitgetPlanOrder(
            order_id=str(item.get("orderId", "")),
            client_oid=str(item.get("clientOid", "")),
            symbol=str(item.get("symbol", "")),
            side=str(item.get("side", "")),
            trade_side=str(item.get("tradeSide", "")),
            plan_type=str(item.get("planType", "")),
            order_type=str(item.get("orderType", "")),
            trigger_price=Decimal(str(item.get("triggerPrice", "0"))),
            size=Decimal(str(item.get("size", "0"))),
            status=str(item.get("status", "")),
            created_at=_parse_ms_timestamp(str(item.get("cTime", "0"))),
        )
        result.append(plan)
    return result


def _parse_ticker(
    data: msgspec.Raw, symbol: str,
) -> BitgetTicker:
    """Parse ticker endpoint data into typed model."""
    raw = msgspec.json.decode(data, type=list[dict[str, str]])
    if not raw:
        return _empty_ticker(symbol)
    item = raw[0]
    return BitgetTicker(
        symbol=item.get("symbol", symbol),
        last_price=Decimal(item.get("lastPr", "0")),
        best_bid=Decimal(item.get("bidPr", "0")),
        best_ask=Decimal(item.get("askPr", "0")),
        high_24h=Decimal(item.get("high24h", "0")),
        low_24h=Decimal(item.get("low24h", "0")),
        volume_24h=Decimal(item.get("baseVolume", "0")),
        timestamp=int(item.get("ts", "0")),
    )


def _empty_ticker(symbol: str) -> BitgetTicker:
    """Return a zero-valued ticker for degraded mode."""
    return BitgetTicker(
        symbol=symbol, last_price=Decimal("0"),
        best_bid=Decimal("0"), best_ask=Decimal("0"),
        high_24h=Decimal("0"), low_24h=Decimal("0"),
        volume_24h=Decimal("0"), timestamp=0,
    )


def _parse_contract_spec(
    data: msgspec.Raw, symbol: str,
) -> BitgetContractSpec:
    """Parse contract spec from market/contracts response."""
    raw = msgspec.json.decode(data, type=list[dict[str, str]])
    if not raw:
        return _empty_contract_spec(symbol)
    item = raw[0]
    return BitgetContractSpec(
        symbol=item.get("symbol", symbol),
        base_coin=item.get("baseCoin", ""),
        quote_coin=item.get("quoteCoin", "USDT"),
        min_trade_num=Decimal(item.get("minTradeNum", "0.001")),
        size_multiplier=Decimal(item.get("sizeMultiplier", "0.001")),
        price_end_step=Decimal(item.get("priceEndStep", "0.1")),
        taker_fee_rate=Decimal(item.get("takerFeeRate", "0.0006")),
        maker_fee_rate=Decimal(item.get("makerFeeRate", "0.0002")),
    )


def _empty_contract_spec(symbol: str) -> BitgetContractSpec:
    """Return conservative defaults for degraded mode."""
    return BitgetContractSpec(
        symbol=symbol, base_coin="", quote_coin="USDT",
        min_trade_num=Decimal("0.001"),
        size_multiplier=Decimal("0.001"),
        price_end_step=Decimal("0.1"),
        taker_fee_rate=Decimal("0.0006"),
        maker_fee_rate=Decimal("0.0002"),
    )


def _parse_order_result(data: msgspec.Raw) -> PlaceOrderResult:
    """Parse write endpoint result."""
    raw = msgspec.json.decode(data, type=dict[str, str])
    return PlaceOrderResult(
        success=True,
        order_id=raw.get("orderId"),
        client_oid=raw.get("clientOid"),
    )


def _quantize_to_step(value: Decimal, step: Decimal) -> Decimal:
    """Round value DOWN to the nearest multiple of step."""
    if step <= 0:
        return value
    return (value / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def _opt_decimal(val: str | None) -> Decimal | None:
    """Parse optional decimal — returns None for empty/zero strings."""
    if not val or val == "0" or val == "":
        return None
    return Decimal(val)


def _parse_ms_timestamp(ms_str: str) -> datetime:
    """Convert millisecond timestamp string to datetime."""
    try:
        ms = int(ms_str)
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (ValueError, OSError):
        return datetime.now(tz=timezone.utc)
