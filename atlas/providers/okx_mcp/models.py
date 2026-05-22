"""OKX MCP provider models — frozen Pydantic models.

All financial fields use Decimal. Never float for price/rate/size.
"""

from decimal import Decimal

from pydantic import BaseModel


class OKXFundingRate(BaseModel, frozen=True):
    """Current live funding rate for a perpetual swap."""

    inst_id: str
    funding_rate: Decimal
    funding_time: int
    next_funding_time: int
    min_funding_rate: Decimal
    max_funding_rate: Decimal
    source: str = "okx_mcp"
    fetched_at_ms: int = 0


class OKXFundingRateBar(BaseModel, frozen=True):
    """Single bar in funding rate history."""

    inst_id: str
    funding_rate: Decimal
    funding_time: int
    realized_rate: Decimal


class OKXFundingHistory(BaseModel, frozen=True):
    """90-bar funding rate history for Z-score calculation."""

    inst_id: str
    bars: list[OKXFundingRateBar]
    source: str = "okx_mcp"


class OKXOpenInterest(BaseModel, frozen=True):
    """Current open interest snapshot."""

    inst_id: str
    oi: Decimal
    oi_ccy: Decimal
    ts: int
    source: str = "okx_mcp"


class OKXOpenInterestBar(BaseModel, frozen=True):
    """Single bar in OI history."""

    inst_id: str
    oi: Decimal
    oi_ccy: Decimal
    ts: int


class OKXOpenInterestHistory(BaseModel, frozen=True):
    """336-bar OI history (14 days at 1h) for high detection."""

    inst_id: str
    bars: list[OKXOpenInterestBar]
    source: str = "okx_mcp"


class OKXLongShortRatio(BaseModel, frozen=True):
    """Long/short ratio snapshot."""

    inst_id: str
    long_short_ratio: Decimal
    long_ratio: Decimal
    short_ratio: Decimal
    ts: int
    source: str = "okx_mcp"


class OKXLiquidationOrder(BaseModel, frozen=True):
    """Single filled liquidation order."""

    inst_id: str
    side: str
    size: Decimal
    bk_px: Decimal
    ts: int


class OKXLiquidationSnapshot(BaseModel, frozen=True):
    """Batch of recent liquidation orders."""

    inst_id: str
    orders: list[OKXLiquidationOrder]
    fetched_at: int
    source: str = "okx_mcp"


class OKXProviderHealth(BaseModel, frozen=True):
    """Health status snapshot for OKX MCP connector."""

    status: str
    last_call_ts: int | None = None
    last_error: str | None = None
    call_count: int = 0
    error_count: int = 0
