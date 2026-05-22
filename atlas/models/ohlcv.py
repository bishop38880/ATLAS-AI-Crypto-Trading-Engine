"""OHLCVCandle — frozen msgspec.Struct for hot-path OHLCV data.

Used by the Shadow Metric Collector (S3-P10) and any future module
that requires candle-level market data without DataFrame overhead.

Architecture note:
    This is a msgspec.Struct (NOT Pydantic) — chosen for hot-path
    performance in tight scoring loops. All financial fields are
    Decimal per Invariant 8.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import msgspec


class OHLCVCandle(msgspec.Struct, frozen=True, kw_only=True):
    """Single OHLCV candle — immutable value object.

    Attributes:
        timestamp: Candle open time (UTC).
        open: Open price.
        high: High price.
        low: Low price.
        close: Close price.
        volume: Trade volume in base currency.
    """

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
