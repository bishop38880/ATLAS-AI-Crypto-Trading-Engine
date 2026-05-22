"""Pydantic v2 frozen output models for Deribit Options MCP tools.

Section 26.3 Architecture — Options Skew Detector.
All tool outputs are strictly typed for agentic consumption by the
DerivativesContext MCP and Tier-1 DerivativesAgent.

Sentinel Invariants:
  - All models frozen=True (immutable DTOs)
  - Financial values (strike_price, notional_usd) use Decimal
  - Dimensionless values (iv, delta, risk_reversal) use float
  - Every field has Field(description=...)
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Options Skew (25-Delta Risk Reversal)
# ---------------------------------------------------------------------------

class SkewEntry(BaseModel, frozen=True):
    """Single expiry 25-delta risk reversal entry."""

    expiry_label: str = Field(
        description="Human-readable expiry label (e.g. '7d', '30d', '90d').",
    )
    expiry_date: str = Field(
        description="ISO-8601 expiry date string.",
    )
    days_to_expiry: int = Field(
        description="Calendar days until expiration.",
    )
    call_iv_25d: float = Field(
        description="Interpolated 25-delta call implied volatility (annualised).",
    )
    put_iv_25d: float = Field(
        description="Interpolated 25-delta put implied volatility (annualised).",
    )
    risk_reversal: float = Field(
        description="25d RR = call_iv_25d - put_iv_25d. Positive = bullish conviction.",
    )
    skew_label: str = Field(
        description="Contextual label for LLM reasoning: 'Bullish Skew', 'Bearish Skew', or 'Neutral'.",
    )


class OptionsSkewResponse(BaseModel, frozen=True):
    """Full 25-delta risk reversal term structure.

    Section 26.3: Primary output for institutional directional conviction gauge.
    """

    coin: str = Field(description="Underlying asset (BTC or ETH).")
    entries: list[SkewEntry] = Field(
        description="Risk reversal entries sorted by days_to_expiry ascending.",
    )
    snapshot_ts: str = Field(
        description="ISO-8601 timestamp of the data snapshot.",
    )
    ws_status: str = Field(
        description="WebSocket connection status: CONNECTED, DISCONNECTED, or INITIALIZING.",
    )


# ---------------------------------------------------------------------------
# Implied Volatility Surface
# ---------------------------------------------------------------------------

class IVSurfacePoint(BaseModel, frozen=True):
    """Single point on the implied volatility surface."""

    expiry_date: str = Field(description="ISO-8601 expiry date.")
    strike_price: Decimal = Field(description="Option strike price in USD.")
    mark_iv: float = Field(
        description="Mark implied volatility (annualised, dimensionless).",
    )
    option_type: str = Field(description="'call' or 'put'.")


class ATMTermEntry(BaseModel, frozen=True):
    """ATM implied volatility for a single expiry."""

    expiry_date: str = Field(description="ISO-8601 expiry date.")
    days_to_expiry: int = Field(description="Calendar days until expiration.")
    atm_iv: float = Field(
        description="At-the-money implied volatility (annualised).",
    )


class IVSurfaceResponse(BaseModel, frozen=True):
    """Full implied volatility surface snapshot.

    Section 26.3: Provides term structure shape detection
    (contango vs backwardation) for the DerivativesAgent.
    """

    coin: str = Field(description="Underlying asset (BTC or ETH).")
    surface_points: list[IVSurfacePoint] = Field(
        description="IV surface grid points (strike × expiry × IV).",
    )
    atm_term_structure: list[ATMTermEntry] = Field(
        description="ATM IV term structure sorted by days_to_expiry ascending.",
    )
    term_structure_shape: str = Field(
        description="'Contango' (normal) or 'Backwardation' (inverted) or 'Flat'.",
    )
    underlying_price: Decimal = Field(
        description="Current underlying index price in USD.",
    )
    snapshot_ts: str = Field(description="ISO-8601 timestamp of the data snapshot.")
    ws_status: str = Field(description="WebSocket status.")


# ---------------------------------------------------------------------------
# Put/Call Ratios
# ---------------------------------------------------------------------------

class PutCallRatioResponse(BaseModel, frozen=True):
    """Aggregate Put/Call ratios for options chain.

    Section 26.3: Volume and OI P/C ratios for sentiment gauging.
    """

    coin: str = Field(description="Underlying asset (BTC or ETH).")
    volume_put_call_ratio: float = Field(
        description="24h volume P/C ratio. >1.0 = more puts traded (bearish tilt).",
    )
    oi_put_call_ratio: float = Field(
        description="Open interest P/C ratio. >1.0 = more put OI (hedging/bearish).",
    )
    total_call_volume: float = Field(description="Total 24h call volume.")
    total_put_volume: float = Field(description="Total 24h put volume.")
    total_call_oi: float = Field(description="Total call open interest.")
    total_put_oi: float = Field(description="Total put open interest.")
    snapshot_ts: str = Field(description="ISO-8601 timestamp of the data snapshot.")
    ws_status: str = Field(description="WebSocket status.")


# ---------------------------------------------------------------------------
# Block Trades
# ---------------------------------------------------------------------------

class BlockTrade(BaseModel, frozen=True):
    """Single institutional block trade record.

    Section 26.3: Trades exceeding institutional thresholds (>$500k notional).
    """

    instrument_name: str = Field(description="Deribit instrument name.")
    direction: str = Field(
        description="Trade direction: 'buy' or 'sell' (taker side).",
    )
    amount: float = Field(description="Contract quantity.")
    price: Decimal = Field(description="Execution price in USD.")
    notional_usd: Decimal = Field(description="Estimated USD notional value.")
    mark_iv: float = Field(
        description="Mark IV at time of execution (dimensionless).",
    )
    timestamp: str = Field(description="ISO-8601 trade timestamp.")
    option_type: str = Field(description="'call' or 'put'.")
    strike_price: Decimal = Field(description="Option strike price in USD.")
    expiry_date: str = Field(description="ISO-8601 expiry date.")


class BlockTradesResponse(BaseModel, frozen=True):
    """Buffered list of recent institutional block trades.

    Section 26.3: Rolling buffer of last 50 block trades for
    aggressive buying/selling pattern detection.
    """

    coin: str = Field(description="Underlying asset (BTC or ETH).")
    trades: list[BlockTrade] = Field(
        description="Recent block trades sorted by timestamp descending.",
    )
    total_count: int = Field(description="Number of block trades in buffer.")
    snapshot_ts: str = Field(description="ISO-8601 timestamp of the data snapshot.")
    ws_status: str = Field(description="WebSocket status.")
