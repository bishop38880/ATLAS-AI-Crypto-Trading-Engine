"""Order book replay engine — loads L3 snapshots from TimescaleDB.

Provides an iterator that yields the book state at each timestamp,
pre-loading data into numpy arrays for vectorised binary search.
Supports both database-backed and in-memory (synthetic) replay modes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator

import numpy as np
from loguru import logger

from prometheus.backtesting.models import OrderBookSnapshot, PriceLevel


# ── OrderBookReplayEngine ────────────────────────────────────────────


class OrderBookReplayEngine:
    """Replays pre-recorded L3 order book snapshots.

    Two modes:
      1. ``from_database()`` — async factory, loads from TimescaleDB.
      2. ``from_snapshots()`` — sync factory, loads from memory (tests).

    After construction, call ``snapshot_at()`` or iterate via
    ``__iter__()`` to step through the book at 100ms granularity.
    """

    def __init__(
        self,
        snapshots: list[OrderBookSnapshot],
        timestamps_epoch_ms: np.ndarray,
    ) -> None:
        self._snapshots = snapshots
        self._ts_ms = timestamps_epoch_ms
        self._length = len(snapshots)
        logger.info(
            "replay_engine_loaded | snapshots={} | span_ms={}",
            self._length,
            int(self._ts_ms[-1] - self._ts_ms[0]) if self._length > 1 else 0,
        )

    # ── Factory Methods ──────────────────────────────────────────────

    @classmethod
    def from_snapshots(
        cls,
        snapshots: list[OrderBookSnapshot],
    ) -> OrderBookReplayEngine:
        """Create engine from pre-built snapshots (tests / synthetic)."""
        ts_ms = _extract_timestamps_ms(snapshots)
        return cls(snapshots=snapshots, timestamps_epoch_ms=ts_ms)

    @classmethod
    async def from_database(
        cls,
        pool: object,
        symbol: str,
        start: datetime,
        end: datetime,
    ) -> OrderBookReplayEngine:
        """Load snapshots from TimescaleDB hypertable.

        Reconstructs book state at each distinct timestamp from raw
        L3 events.  Requires ``asyncpg.Pool``.
        """
        snapshots = await _load_from_timescale(pool, symbol, start, end)
        ts_ms = _extract_timestamps_ms(snapshots)
        return cls(snapshots=snapshots, timestamps_epoch_ms=ts_ms)

    # ── Query API ────────────────────────────────────────────────────

    @property
    def length(self) -> int:
        """Number of loaded snapshots."""
        return self._length

    @property
    def start_time(self) -> datetime:
        """Timestamp of first snapshot."""
        return self._snapshots[0].timestamp if self._length > 0 else _EPOCH

    @property
    def end_time(self) -> datetime:
        """Timestamp of last snapshot."""
        return self._snapshots[-1].timestamp if self._length > 0 else _EPOCH

    def snapshot_at(self, timestamp: datetime) -> OrderBookSnapshot:
        """Return the book snapshot closest to (but not after) timestamp.

        Uses numpy-accelerated binary search for O(log n) lookup.
        """
        target_ms = _datetime_to_epoch_ms(timestamp)
        idx = int(np.searchsorted(self._ts_ms, target_ms, side="right")) - 1
        idx = max(0, min(idx, self._length - 1))
        return self._snapshots[idx]

    def __iter__(self) -> Iterator[tuple[datetime, OrderBookSnapshot]]:
        """Yield ``(timestamp, snapshot)`` for every loaded tick."""
        for snap in self._snapshots:
            yield snap.timestamp, snap

    def __len__(self) -> int:
        return self._length


# ── Epoch Helpers ────────────────────────────────────────────────────

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _datetime_to_epoch_ms(dt: datetime) -> float:
    """Convert datetime to epoch milliseconds."""
    return dt.timestamp() * 1000.0


def _extract_timestamps_ms(
    snapshots: list[OrderBookSnapshot],
) -> np.ndarray:
    """Build a sorted numpy array of epoch-ms timestamps."""
    if not snapshots:
        return np.array([], dtype=np.float64)
    return np.array(
        [_datetime_to_epoch_ms(s.timestamp) for s in snapshots],
        dtype=np.float64,
    )


# ── TimescaleDB Loader ──────────────────────────────────────────────


async def _load_from_timescale(
    pool: object,
    symbol: str,
    start: datetime,
    end: datetime,
) -> list[OrderBookSnapshot]:
    """Query l3_order_book hypertable, group by time, build snapshots.

    Uses asyncpg pool.  Groups L3 events into per-timestamp snapshots.
    """
    query = """
        SELECT time, side, price, size
        FROM l3_order_book
        WHERE symbol = $1 AND time >= $2 AND time < $3
        ORDER BY time ASC, side ASC, price ASC
    """
    rows = await pool.fetch(query, symbol, start, end, timeout=30)  # type: ignore[union-attr]
    return _rows_to_snapshots(rows, symbol)


def _rows_to_snapshots(
    rows: list[object],
    symbol: str,
) -> list[OrderBookSnapshot]:
    """Convert raw DB rows into grouped OrderBookSnapshot list."""
    if not rows:
        return []

    snapshots: list[OrderBookSnapshot] = []
    current_time: datetime | None = None
    bids: list[PriceLevel] = []
    asks: list[PriceLevel] = []

    for row in rows:
        row_time = row["time"]  # type: ignore[index]
        if current_time is not None and row_time != current_time:
            snap = _finalise_snapshot(symbol, current_time, bids, asks)
            snapshots.append(snap)
            bids, asks = [], []
        current_time = row_time
        level = PriceLevel(
            price=Decimal(str(row["price"])),  # type: ignore[index]
            size=Decimal(str(row["size"])),  # type: ignore[index]
        )
        if row["side"] == "bid":  # type: ignore[index]
            bids.append(level)
        else:
            asks.append(level)

    if current_time is not None:
        snapshots.append(_finalise_snapshot(symbol, current_time, bids, asks))

    return snapshots


def _finalise_snapshot(
    symbol: str,
    timestamp: datetime,
    bids: list[PriceLevel],
    asks: list[PriceLevel],
) -> OrderBookSnapshot:
    """Sort levels and build an immutable snapshot."""
    sorted_bids = sorted(bids, key=lambda lv: lv.price, reverse=True)
    sorted_asks = sorted(asks, key=lambda lv: lv.price)
    return OrderBookSnapshot(
        symbol=symbol,
        timestamp=timestamp,
        bids=sorted_bids,
        asks=sorted_asks,
    )
