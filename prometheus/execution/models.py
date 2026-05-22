"""Pydantic v2 response models for Bitget V2 perpetual futures endpoints.

Every endpoint gets a frozen model.  No ``dict[str, Any]`` returns.
All financial fields use ``Decimal``.  ``float`` only for latency.

Architecture note:
    These models represent the *application-layer* view after parsing
    the raw Bitget JSON envelope.  The raw envelope itself is a
    ``msgspec.Struct`` (see ``BitgetEnvelope`` below) to satisfy
    Invariant 8 (msgspec for response parsing).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

import msgspec
from pydantic import BaseModel, Field


# ──────────────────────────────────────────────────────────────────────
# msgspec envelope — raw Bitget wrapper parsed first
# ──────────────────────────────────────────────────────────────────────


class BitgetEnvelope(msgspec.Struct, frozen=True):
    """Standard Bitget V2 JSON response wrapper.

    Attributes:
        code: Status code — ``"00000"`` means success.
        msg: Human-readable message (``"success"`` on OK).
        data: Raw JSON payload — decoded separately into typed model.
        requestTime: Server-side request timestamp in ms.
    """

    code: str
    msg: str
    data: msgspec.Raw = msgspec.Raw(b"null")
    requestTime: int = 0


# ──────────────────────────────────────────────────────────────────────
# Read endpoint response models
# ──────────────────────────────────────────────────────────────────────


class BitgetAccountEquity(BaseModel, frozen=True):
    """Single-asset account detail from GET /api/v2/mix/account/account."""

    margin_coin: str = Field(description="Margin currency, e.g. USDT")
    available: Decimal = Field(description="Available balance for trading")
    equity: Decimal = Field(
        description="Total account equity (used as portfolio_capital_usd)"
    )
    unrealized_pnl: Decimal = Field(description="Unrealized P&L across positions")
    locked: Decimal = Field(description="Locked margin in open positions")


class BitgetPosition(BaseModel, frozen=True):
    """Position detail from GET /api/v2/mix/position/single-position."""

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    product_type: str = Field(description="Product type, e.g. USDT-FUTURES")
    margin_mode: Literal["isolated", "crossed"] = Field(
        description="Margin mode for this position"
    )
    margin_coin: str = Field(description="Margin currency, e.g. USDT")
    side: Literal["long", "short"] = Field(description="Position direction")
    total: Decimal = Field(description="Position size in BASE COIN")
    available: Decimal = Field(description="Closeable size in base coin")
    leverage: Decimal = Field(description="Current leverage multiplier")
    average_open_price: Decimal = Field(
        description=(
            "Weighted average entry price — use this for reconciliation, "
            "NOT markPrice"
        )
    )
    mark_price: Decimal = Field(
        description="Current mark price (moves continuously)"
    )
    unrealized_pnl: Decimal = Field(description="Unrealized PnL in USDT")
    margin_size: Decimal = Field(
        description="Margin locked for this position"
    )
    liquidation_price: Decimal | None = Field(
        default=None, description="Liquidation price if known"
    )
    created_at: datetime = Field(description="Position open timestamp")


class BitgetContractSpec(BaseModel, frozen=True):
    """Contract specification from GET /api/v2/mix/market/contracts.

    Used to correctly convert USD notional to base-coin size and
    validate price increments before submitting orders.
    """

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    base_coin: str = Field(description="Base currency, e.g. BTC")
    quote_coin: str = Field(description="Quote currency, e.g. USDT")
    min_trade_num: Decimal = Field(
        description="Minimum order size in base coin"
    )
    size_multiplier: Decimal = Field(
        description="Size step — order size must be a multiple"
    )
    price_end_step: Decimal = Field(
        description="Price step — order price must be a multiple"
    )
    taker_fee_rate: Decimal = Field(description="Taker fee rate as decimal")
    maker_fee_rate: Decimal = Field(description="Maker fee rate as decimal")


class BitgetTicker(BaseModel, frozen=True):
    """Ticker from GET /api/v2/mix/market/ticker."""

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    last_price: Decimal = Field(description="Latest trade price")
    best_bid: Decimal = Field(description="Best bid price")
    best_ask: Decimal = Field(description="Best ask price")
    high_24h: Decimal = Field(description="24-hour high")
    low_24h: Decimal = Field(description="24-hour low")
    volume_24h: Decimal = Field(description="24-hour volume in base coin")
    timestamp: int = Field(description="Server timestamp in milliseconds")


class BitgetOpenOrder(BaseModel, frozen=True):
    """Open order from GET /api/v2/mix/order/orders-pending."""

    order_id: str = Field(description="Bitget-assigned order ID")
    client_oid: str = Field(default="", description="Client-set custom ID")
    symbol: str = Field(description="Trading pair")
    side: str = Field(description="buy or sell")
    order_type: str = Field(description="limit or market")
    price: Decimal = Field(description="Order price (0 for market)")
    size: Decimal = Field(description="Order size in base coin")
    status: str = Field(description="Order status")
    created_at: datetime = Field(description="Order creation timestamp")


class BitgetPlanOrder(BaseModel, frozen=True):
    """Plan/trigger order from GET /api/v2/mix/order/orders-plan-pending."""

    order_id: str = Field(description="Bitget-assigned plan order ID")
    client_oid: str = Field(default="", description="Client-set custom ID")
    symbol: str = Field(description="Trading pair")
    side: str = Field(description="buy or sell")
    trade_side: str = Field(description="open or close")
    plan_type: str = Field(
        description="normal_plan / track_plan / profit_plan / loss_plan"
    )
    order_type: str = Field(description="limit or market")
    trigger_price: Decimal = Field(description="Trigger price level")
    size: Decimal = Field(description="Order size in base coin")
    status: str = Field(description="Plan order status")
    created_at: datetime = Field(description="Creation timestamp")


# ──────────────────────────────────────────────────────────────────────
# Write endpoint request models
# ──────────────────────────────────────────────────────────────────────


class PlaceOrderRequest(BaseModel, frozen=True):
    """Request body for POST /api/v2/mix/order/place-order."""

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    product_type: str = Field(
        default="USDT-FUTURES", description="Product type"
    )
    margin_mode: Literal["isolated"] = Field(
        default="isolated", description="Always isolated — cross prohibited"
    )
    margin_coin: str = Field(default="USDT", description="Margin currency")
    size: Decimal = Field(description="Order size in BASE COIN, not USD")
    price: Decimal | None = Field(
        default=None, description="Limit price — required for limit orders"
    )
    side: Literal["buy", "sell"] = Field(description="Order side")
    trade_side: Literal["open", "close"] = Field(
        default="open", description="Open or close position"
    )
    order_type: Literal["market", "limit"] = Field(
        description="Order type"
    )
    client_oid: str | None = Field(
        default=None, description="Client-set custom order ID"
    )


class PlacePlanOrderRequest(BaseModel, frozen=True):
    """Request body for POST /api/v2/mix/order/place-plan-order.

    This is for standalone price-triggered orders (stop ladder).
    NOT the same as place-tpsl-order (position-attached TP/SL).
    """

    symbol: str = Field(description="Trading pair, e.g. BTCUSDT")
    product_type: str = Field(
        default="USDT-FUTURES", description="Product type"
    )
    margin_mode: Literal["isolated"] = Field(
        default="isolated", description="Always isolated"
    )
    margin_coin: str = Field(default="USDT", description="Margin currency")
    size: Decimal = Field(description="Order size in BASE COIN, not USD")
    price: Decimal | None = Field(
        default=None, description="Limit execution price (required for limit)"
    )
    side: Literal["buy", "sell"] = Field(description="Order side")
    trade_side: Literal["open", "close"] = Field(
        description="Open new or close existing position"
    )
    trigger_price: Decimal = Field(description="Price to trigger the order")
    trigger_type: Literal["mark_price", "fill_price"] = Field(
        description="Trigger reference price type"
    )
    order_type: Literal["market", "limit"] = Field(
        description="Execution order type after trigger"
    )
    plan_type: Literal[
        "normal_plan", "track_plan", "profit_plan", "loss_plan"
    ] = Field(description="Plan order category — MUST NOT be omitted")
    client_oid: str | None = Field(
        default=None, description="Client-set custom order ID"
    )


class PlaceTpslOrderRequest(BaseModel, frozen=True):
    """Request body for POST /api/v2/mix/order/place-tpsl-order.

    Position-attached TP/SL plans.  Different from place-plan-order.
    Used when a position is already open.
    """

    symbol: str = Field(description="Trading pair")
    product_type: str = Field(
        default="USDT-FUTURES", description="Product type"
    )
    margin_mode: Literal["isolated"] = Field(
        default="isolated", description="Always isolated"
    )
    plan_type: Literal["pos_profit", "pos_loss"] = Field(
        description="Position TP or SL"
    )
    trigger_price: Decimal = Field(description="Trigger price")
    trigger_type: Literal["mark_price", "fill_price"] = Field(
        default="mark_price", description="Trigger reference"
    )
    size: Decimal | None = Field(
        default=None,
        description="Partial close size in base coin (None = full position)",
    )
    client_oid: str | None = Field(
        default=None, description="Client-set custom order ID"
    )


class ModifyPlanOrderRequest(BaseModel, frozen=True):
    """Request body for POST /api/v2/mix/order/modify-plan-order."""

    order_id: str = Field(description="Existing plan order ID to modify")
    product_type: str = Field(
        default="USDT-FUTURES", description="Product type"
    )
    trigger_price: Decimal | None = Field(
        default=None, description="New trigger price"
    )
    size: Decimal | None = Field(
        default=None, description="New size in base coin"
    )
    order_type: Literal["market", "limit"] | None = Field(
        default=None, description="New order type"
    )
    price: Decimal | None = Field(
        default=None, description="New limit price"
    )


# ──────────────────────────────────────────────────────────────────────
# Write endpoint result model
# ──────────────────────────────────────────────────────────────────────


class PlaceOrderResult(BaseModel, frozen=True):
    """Unified result for all write endpoints."""

    success: bool = Field(description="Whether the operation succeeded")
    order_id: str | None = Field(
        default=None, description="Bitget-assigned order ID"
    )
    client_oid: str | None = Field(
        default=None, description="Client-set custom order ID"
    )
    errors: list[str] = Field(
        default_factory=list, description="Error messages if any"
    )
    paper_trading: bool = Field(
        default=False, description="True if this was a paper-mode stub"
    )


# ──────────────────────────────────────────────────────────────────────
# Custom exceptions
# ──────────────────────────────────────────────────────────────────────


class InsufficientSizeError(Exception):
    """Raised when converted base-coin size is below min_trade_num.

    The caller decides whether to round up to minimum or reject.
    ``usd_notional_to_base_coin_size`` rounds UP to min — this error
    is raised only when even min_trade_num exceeds a safety threshold
    (e.g. the order would be 10× smaller than minimum).
    """
