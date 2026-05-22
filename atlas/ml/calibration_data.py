import asyncpg
from datetime import datetime, timezone
from decimal import Decimal
from pydantic import BaseModel, Field


class CalibrationDataset(BaseModel, frozen=True):
    raw_scores: list[float]
    outcomes: list[int]
    n_samples: int = Field(ge=0)
    date_range: tuple[datetime, datetime]


class InsufficientDataError(Exception):
    pass


class CalibrationDataBuilder:
    def __init__(self, asyncpg_pool: asyncpg.Pool) -> None:
        self._pool = asyncpg_pool

    async def build(self, min_samples: int = 500) -> CalibrationDataset:
        """Query PostgreSQL via asyncpg for historical signals with outcomes."""
        rows = await self._query_calibration_rows()

        if len(rows) < min_samples:
            raise InsufficientDataError(
                f"Found {len(rows)} samples, need {min_samples}"
            )

        return self._parse_calibration_rows(rows)

    async def _query_calibration_rows(self) -> list[asyncpg.Record]:
        """Fetch historical signals joined with trade outcomes."""
        async with self._pool.acquire() as conn:
            return await conn.fetch(
                """
                SELECT s.score, t.pnl_pct, s.timestamp
                FROM signal_history s
                JOIN trade_signals t ON s.signal_id = t.signal_id
                WHERE t.pnl_pct IS NOT NULL
                ORDER BY s.timestamp ASC
                """,
                timeout=10.0,
            )

    def _parse_calibration_rows(
        self, rows: list[asyncpg.Record],
    ) -> CalibrationDataset:
        """Parse DB rows into a CalibrationDataset."""
        raw_scores: list[float] = []
        outcomes: list[int] = []
        min_date: datetime | None = None
        max_date: datetime | None = None

        for row in rows:
            score = float(row["score"])
            pnl = Decimal(str(row["pnl_pct"]))
            ts = row["timestamp"]

            raw_scores.append(score)
            outcomes.append(1 if pnl > Decimal("0") else 0)

            if min_date is None or ts < min_date:
                min_date = ts
            if max_date is None or ts > max_date:
                max_date = ts

        if not min_date or not max_date:
            min_date = max_date = datetime.now(timezone.utc)

        return CalibrationDataset(
            raw_scores=raw_scores,
            outcomes=outcomes,
            n_samples=len(raw_scores),
            date_range=(min_date, max_date),
        )
