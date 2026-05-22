# SESSION 22B — Bitget Execution Client (Full-Featured)

## Context Files
`@atlas/shared/config.py  @schema.sql  @prometheus/shared/bitget_signing.py`
`@POLARIS_Context_Document_v2.1.md`

**Prerequisite:** Session 22 (Kill Switch) complete. This session reuses
the shared `bitget_signing.py` helper created there.

---

## Hard-Wall Invariants (restated)

1. POLARIS / PROMETHEUS hard wall — this client is PROMETHEUS-only.
2. `httpx.AsyncClient` with HTTP/2, singleton per process, shared via DI.
3. `redis.asyncio` (not `aioredis`). `msgspec` (not stdlib `json`).
   `asyncpg` (not SQLAlchemy). `pyright` (not `mypy`). Loguru positional.
4. `Decimal` for all financial fields (prices, sizes, notionals, fees,
   equity, leverage). `float` only for latency and confidence.
5. `PolarisSettings` for all config — no `os.getenv()`.
6. Max 40 lines per function. Pydantic v2 frozen models with
   `Field(description=...)` on every field.
7. Every endpoint call wrapped in a `pybreaker` circuit breaker.
8. All response parsing via `msgspec.json.decode(..., type=TypedResponse)`.
9. pytest floor — read current count at session start; must not decrease.

Confirm all 9 invariants, then I will give the task.

---

## Why This Session Exists

Session 22's `BitgetDirectClient` is deliberately minimal — three
emergency endpoints, isolated from the rest of the codebase so it
works when everything else is broken.

Sessions 23–27 need richer Bitget operations that are NOT safe to add
to the kill switch's minimal client (because every additional line of
code in the kill switch is code that could break the emergency path).

This session provides `BitgetExecutionClient`, the full-featured
Bitget V2 REST client used by the paper-trading engine, reconciler,
risk enforcer, and stop-placement modules.

---

## Scope — Endpoints Required

### Read endpoints (GET)

- `/api/v2/mix/account/account` — single-asset account detail (equity,
  available, locked, unrealizedPnL).
- `/api/v2/mix/account/accounts` — all assets.
- `/api/v2/mix/position/single-position` — one symbol, returns
  averageOpenPrice, markPrice, marginMode, leverage, marginSize, total.
- `/api/v2/mix/position/all-position` — all open positions for a productType.
- `/api/v2/mix/order/orders-pending` — open orders list.
- `/api/v2/mix/order/orders-plan-pending` — open plan/trigger orders.
- `/api/v2/mix/market/ticker` — latest trade price for a symbol.
- `/api/v2/mix/market/contracts` — contract specification (lotSize,
  minTradeNum, priceStep, feeRate) — used to correctly convert USD
  notional to base-coin size.

### Write endpoints (POST)

- `/api/v2/mix/order/place-order` — market / limit orders.
- `/api/v2/mix/order/place-plan-order` — standalone price-triggered
  orders (used for stop ladders in Session 23-27 Task 2).
- `/api/v2/mix/order/place-tpsl-order` — position-attached TP/SL plans
  (used when a position is already open).
- `/api/v2/mix/order/cancel-plan-order` — cancel a specific plan order.
- `/api/v2/mix/order/modify-plan-order` — modify trigger price or size.

**Explicit distinction:** `place-plan-order` and `place-tpsl-order` are
different endpoints with different required fields. Session 23-27's
stop ladder uses `place-plan-order` (not `place-tpsl-order` as the
original draft had) because stop ladder entries are standalone
trigger orders, not position-attached TP/SL plans.

Required `place-plan-order` body fields (verify against current docs):
- `symbol`, `productType`, `marginMode` ("isolated"), `marginCoin`
- `size` (in BASE COIN, not USD), `price` (limit only)
- `side` ("buy" / "sell"), `tradeSide` ("open" / "close"),
- `triggerPrice`, `triggerType` ("mark_price" / "fill_price"),
- `orderType` ("market" / "limit"),
- `planType` ("normal_plan" / "track_plan" / "profit_plan" / "loss_plan")

---

## Task 1 — Response Models

Create `prometheus/execution/models.py`:

Every endpoint gets a Pydantic v2 frozen model. Examples:

```python
from decimal import Decimal
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field

class BitgetPosition(BaseModel, frozen=True):
    symbol: str = Field(description="e.g. BTCUSDT")
    product_type: str = Field(description="USDT-FUTURES")
    margin_mode: Literal["isolated", "crossed"] = Field(description="Margin mode")
    margin_coin: str = Field(description="USDT")
    side: Literal["long", "short"] = Field(description="Position direction")
    total: Decimal = Field(description="Position size in BASE COIN")
    available: Decimal = Field(description="Closeable size in base coin")
    leverage: Decimal = Field(description="Current leverage")
    average_open_price: Decimal = Field(
        description="Weighted average entry price — use this for reconciliation, NOT markPrice"
    )
    mark_price: Decimal = Field(description="Current mark price (moves continuously)")
    unrealized_pnl: Decimal = Field(description="Unrealized PnL in USDT")
    margin_size: Decimal = Field(description="Margin locked for this position")
    liquidation_price: Decimal | None = Field(default=None, description="Liq price if known")
    created_at: datetime


class BitgetContractSpec(BaseModel, frozen=True):
    symbol: str
    base_coin: str = Field(description="e.g. BTC")
    quote_coin: str = Field(description="e.g. USDT")
    min_trade_num: Decimal = Field(description="Minimum order size in base coin")
    size_multiplier: Decimal = Field(description="Size step — size must be a multiple")
    price_end_step: Decimal = Field(description="Price step — price must be a multiple")
    taker_fee_rate: Decimal
    maker_fee_rate: Decimal


class BitgetAccountEquity(BaseModel, frozen=True):
    margin_coin: str = Field(description="USDT")
    available: Decimal = Field(description="Available balance")
    equity: Decimal = Field(description="Total account equity (used as portfolio_capital_usd)")
    unrealized_pnl: Decimal
    locked: Decimal


class PlaceOrderResult(BaseModel, frozen=True):
    success: bool
    order_id: str | None = None
    client_oid: str | None = None
    errors: list[str] = Field(default_factory=list)
    paper_trading: bool = False
```

Write a model for every endpoint response. No `dict[str, Any]` returns.

---

## Task 2 — Client Implementation

Create `prometheus/execution/bitget_client.py`:

```python
class BitgetExecutionClient:
    """Full-featured Bitget V2 perpetual futures client.

    Scope: read account/positions/orders/market, place orders and plans,
    cancel and modify plans. Emergency halt operations live in the
    separate kill_switch module and are intentionally duplicated there.
    """

    def __init__(
        self,
        settings: "PolarisSettings",
        http_client: httpx.AsyncClient,
        circuit_breaker_factory: "CircuitBreakerFactory",
    ) -> None: ...

    # Read
    async def get_account_equity(self, margin_coin: str = "USDT") -> BitgetAccountEquity: ...
    async def get_position(self, symbol: str, product_type: str = "USDT-FUTURES") -> BitgetPosition | None: ...
    async def list_open_positions(self, product_type: str = "USDT-FUTURES") -> list[BitgetPosition]: ...
    async def list_open_orders(self, product_type: str = "USDT-FUTURES") -> list[BitgetOpenOrder]: ...
    async def list_open_plan_orders(self, product_type: str = "USDT-FUTURES") -> list[BitgetPlanOrder]: ...
    async def get_ticker(self, symbol: str) -> BitgetTicker: ...
    async def get_contract_spec(self, symbol: str) -> BitgetContractSpec: ...

    # Write
    async def place_order(self, req: PlaceOrderRequest) -> PlaceOrderResult: ...
    async def place_plan_order(self, req: PlacePlanOrderRequest) -> PlaceOrderResult: ...
    async def place_tpsl_order(self, req: PlaceTpslOrderRequest) -> PlaceOrderResult: ...
    async def cancel_plan_order(self, order_id: str, product_type: str = "USDT-FUTURES") -> bool: ...
    async def modify_plan_order(self, req: ModifyPlanOrderRequest) -> PlaceOrderResult: ...

    # Helpers
    async def usd_notional_to_base_coin_size(
        self, symbol: str, notional_usd: Decimal, reference_price: Decimal | None = None,
    ) -> Decimal:
        """Convert USD notional to base-coin size, rounded to contract spec.

        size_base = (notional_usd / price).quantize(size_multiplier).
        If reference_price is None, fetches ticker.
        Respects min_trade_num and size_multiplier from the contract spec.
        Used by stop ladder, place_order, and reconciliation size comparisons.
        """

    def _sign_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        """Delegates to prometheus/shared/bitget_signing.sign_request()."""
```

### Critical Implementation Details

**Signing:** Import and use the shared helper from Session 22 at
`prometheus/shared/bitget_signing.py`. Do NOT duplicate the signing
logic. One canonical implementation.

**Body serialisation: msgspec cannot natively encode Pydantic objects or Decimals. You MUST serialize the request by calling req.model_dump(mode="json") to convert the Pydantic model and its Decimals into a JSON-safe dictionary, then use msgspec.json.encode(payload_dict) which returns bytes; pass as content=body_bytes to httpx (NOT json= — that invokes httpx's internal stdlib json encoder and double-encodes).
**Response parsing:** `msgspec.json.decode(response.content, type=BitgetEnvelope)`
where `BitgetEnvelope` is the standard Bitget wrapper
`{code, msg, data, requestTime}`. Inspect `code == "00000"` for success.

**Paper trading contract:** When `settings.paper_trading is True`, every
write endpoint returns a realistic typed response WITHOUT hitting the
wire. Read endpoints can be paper-stubbed with a fixture file or
delegated to a `PaperBitgetAdapter` — your choice, document which.

**Circuit breakers:** One breaker per endpoint category (read / write).
On breaker open, return a DEGRADED error response; do NOT raise to the
caller. Caller decides halt vs. fall-back. The breaker factory is
injected so tests can control breaker state.

**Unit conversion discipline:** Bitget perpetual `size` field is in
**base coin**, not USD. Every internal USD → size conversion goes
through `usd_notional_to_base_coin_size()`. Direct `size=str(usd_amount)`
is forbidden — this was the Session 23-27 Task 2 stop-placement bug.

**Reconciliation pattern:**
- `BitgetPosition.total` is in base coin.
- `BitgetPosition.average_open_price` is the WEIGHTED AVERAGE entry.
- Notional at entry = `total * average_open_price`.
- OMS-recorded size_usd was captured at entry → compare against
  `total * average_open_price`, NEVER `total * mark_price` (mark moves).
- Document this in the reconciler module that calls this client.

---

## Task 3 — Rate Limit Accounting

Bitget V2 has per-UID and per-IP rate limits. Track both:

- Read `X-BAPI-LIMIT-STATUS`, `X-BAPI-LIMIT`, `X-BAPI-LIMIT-RESET-TIMESTAMP`
  response headers (verify exact header names against docs).
- Publish remaining budget to Redis key `bitget:rate_limit:{uid}` TTL 60s.
- Log WARNING when remaining < 20% of limit.
- If rate limit hit (429 or specific Bitget error code), open the
  circuit breaker for 30 seconds and return DEGRADED to caller.

---

## Task 4 — Test Suite

`prometheus/execution/test_bitget_client.py`:

Use `pytest-httpx` to mock Bitget responses. Record real API responses
(redacted) into fixture files under `prometheus/execution/fixtures/`.

Required tests:

1. `test_get_position_parses_average_open_price_as_decimal` — no `float`.
2. `test_get_position_returns_none_when_no_position_for_symbol`.
3. `test_usd_notional_to_base_coin_size_respects_min_trade_num` —
   $5 notional on a contract with min_trade_num=0.001 BTC at $70k/BTC
   should return 0.001 BTC (rounded up to minimum) or raise
   `InsufficientSizeError` — document and test which.
4. `test_usd_notional_to_base_coin_size_respects_size_multiplier` —
   $12345 at $70000/BTC with multiplier 0.001 → 0.176 BTC, not 0.1763571...
5. `test_place_plan_order_body_includes_planType` — must not omit this
   field (the Session 23-27 draft did omit it).
6. `test_place_plan_order_size_in_base_coin_not_usd` — critical bug guard.
7. `test_paper_trading_returns_typed_response` — no bare integers.
8. `test_circuit_breaker_opens_on_repeated_5xx` — uses mock breaker.
9. `test_msgspec_encode_used_not_stdlib_json` — AST-scan source.
10. `test_shared_signing_helper_imported_not_duplicated` — grep assertion.
11. `test_bitget_envelope_parse_error_returns_degraded` — garbage response.
12. `test_rate_limit_429_opens_breaker_and_returns_degraded`.
13. `test_reconciliation_uses_average_open_price` — integration-style.

---

## Task 5 — Wire Into Existing Sessions

**Update references in Session 23-27 Phase 0 bundle:**

Every `BitgetDirectClient` reference in the stop ladder, reconciler,
and paper-trading engine should be changed to `BitgetExecutionClient`.

The reconciler size-divergence check in Session 23-27 Task 2 changes from:
```python
# WRONG (original draft)
bitget_size = bitget_pos.size * Decimal("1")  # "contracts to USD (approx)"
```
to:
```python
# CORRECT
bitget_notional_at_entry = bitget_pos.total * bitget_pos.average_open_price
oms_size = Decimal(str(oms_pos["size_usd"]))
divergence = abs(oms_size - bitget_notional_at_entry) / oms_size
```

The stop ladder in Session 23-27 Task 2 changes from building a raw
JSON body to calling:
```python
size_base = await self._bitget.usd_notional_to_base_coin_size(
    symbol=f"{asset}USDT", notional_usd=tier.size_usd
)
result = await self._bitget.place_plan_order(PlacePlanOrderRequest(
    symbol=f"{asset}USDT",
    product_type="USDT-FUTURES",
    margin_mode="isolated",
    margin_coin="USDT",
    size=size_base,
    side="sell" if position_side == "long" else "buy",
    trade_side="close",
    trigger_price=tier.price,
    trigger_type="mark_price",
    order_type="market",
    plan_type="normal_plan",
))
```

---

## Quality Gates

1. `pytest prometheus/execution/ -v` — all tests pass including the
   13 required tests above.
2. `grep -rn "BitgetDirectClient" prometheus/` — MUST return only
   kill-switch module references. Session 23-27 modules must use
   `BitgetExecutionClient`.
3. `grep -rn "bitget_pos.size \* Decimal" prometheus/` — MUST return zero.
4. `grep -rn 'size=str(.*usd' prometheus/execution/ prometheus/reconciliation/ prometheus/stops/` —
   MUST return zero (no USD-as-size bugs).
5. `grep -rn "aioredis\|import json\b\|orjson\|os.getenv\|SQLAlchemy" prometheus/execution/` —
   MUST return zero.
6. `grep -rn "average_open_price\|averageOpenPrice" prometheus/reconciliation/` —
   MUST return at least one hit (reconciler uses entry price).
7. `pyright prometheus/execution/ --pythonversion 3.12` — zero errors.
8. pytest floor must not decrease.

---

## Notes for the Agent

- This client will be used from multiple places in Sessions 23-27. The
  httpx.AsyncClient must be a singleton injected at app startup, not
  created per call. The original Session 23-27 draft created
  `async with httpx.AsyncClient()` inside every call — reject that
  pattern if Antigravity tries to reproduce it.
- Bitget V2 endpoint paths and field names should be verified against
  current Bitget V2 docs before implementation. If any documented
  endpoint has moved or a field has been renamed, update this session
  doc AND the client module together.
- The kill switch (Session 22) and this execution client share EXACTLY
  ONE module: `prometheus/shared/bitget_signing.py`. If you find
  yourself adding a second shared import, stop and discuss —
  there's a reason the kill switch is isolated.
