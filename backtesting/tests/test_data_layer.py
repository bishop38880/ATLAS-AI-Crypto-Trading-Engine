"""Tests for the backtesting data layer (BT-01)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import polars as pl
import pytest

from backtesting.data.bitget_fetcher import (
    BitgetHistoricalFetcher,
    BitgetNoDataError,
)
from backtesting.data.db import BacktestDB
from backtesting.data.models import FundingRateBar, OHLCVBar
from backtesting.data.synthetic import SyntheticDataGenerator


def _sample_ohlcv_bar(asset: str = "BTCUSDT", timestamp: str = "2024-01-01T00:00:00+00:00") -> OHLCVBar:
    return OHLCVBar(
        asset=asset,
        timestamp_utc=timestamp,
        open=Decimal("100.0"),
        high=Decimal("110.0"),
        low=Decimal("90.0"),
        close=Decimal("105.0"),
        volume=Decimal("12.5"),
        volume_usd=Decimal("1312.5"),
        timeframe="1h",
    )


def _sample_funding_bar(asset: str = "BTCUSDT", timestamp: str = "2024-01-01T00:00:00+00:00") -> FundingRateBar:
    rate = Decimal("0.0001")
    return FundingRateBar(
        asset=asset,
        timestamp_utc=timestamp,
        funding_rate=rate,
        funding_rate_annualised=rate * Decimal("3") * Decimal("365"),
        open_interest_usd=None,
    )


class TestOHLCVBarModel:
    """Validation tests for OHLCVBar."""

    def test_valid_bar_constructs(self) -> None:
        bar = _sample_ohlcv_bar()
        assert bar.asset == "BTCUSDT"
        assert bar.close == Decimal("105.0")

    def test_model_is_frozen(self) -> None:
        bar = _sample_ohlcv_bar()
        with pytest.raises(Exception):
            bar.close = Decimal("200.0")  # type: ignore[misc]


@pytest.fixture
def temp_db(tmp_path: Path) -> BacktestDB:
    db_path = tmp_path / "test_backtest.duckdb"
    return BacktestDB(db_path=db_path)


class TestBacktestDB:
    """DuckDB persistence and query tests."""

    def test_schema_creates_on_fresh_database(self, temp_db: BacktestDB) -> None:
        tables = temp_db._connection.execute("SHOW TABLES").fetchall()
        names = {str(row[0]) for row in tables}
        assert {"ohlcv", "funding_rates", "liquidations", "backtest_runs"}.issubset(names)

    def test_write_ohlcv_ignores_duplicates(self, temp_db: BacktestDB) -> None:
        bars = [_sample_ohlcv_bar(), _sample_ohlcv_bar()]
        inserted_first = temp_db.write_ohlcv(bars)
        inserted_second = temp_db.write_ohlcv(bars)
        assert inserted_first == 1
        assert inserted_second == 0

    def test_get_ohlcv_returns_sorted_polars_frame(self, temp_db: BacktestDB) -> None:
        bars = [
            _sample_ohlcv_bar(timestamp="2024-01-01T01:00:00+00:00"),
            _sample_ohlcv_bar(timestamp="2024-01-01T00:00:00+00:00"),
        ]
        temp_db.write_ohlcv(bars)
        frame = temp_db.get_ohlcv(
            "BTCUSDT",
            "1h",
            "2024-01-01T00:00:00+00:00",
            "2024-01-02T00:00:00+00:00",
        )
        assert isinstance(frame, pl.DataFrame)
        timestamps = frame.get_column("timestamp_utc").to_list()
        assert timestamps == sorted(timestamps)

    def test_get_assets_with_full_coverage_filters_correctly(self, temp_db: BacktestDB) -> None:
        temp_db.write_ohlcv([
            _sample_ohlcv_bar(asset="BTCUSDT"),
            _sample_ohlcv_bar(
                asset="ETHUSDT",
                timestamp="2024-01-01T00:00:00+00:00",
            ),
        ])
        temp_db.write_funding_rates([_sample_funding_bar(asset="BTCUSDT")])
        covered = temp_db.get_assets_with_full_coverage(
            "2024-01-01T00:00:00+00:00",
            "2024-01-02T00:00:00+00:00",
            timeframe="1h",
            require_funding=True,
        )
        assert covered == ["BTCUSDT"]


class TestSyntheticDataGenerator:
    """Synthetic GBM + Markov generator tests."""

    def test_generate_ohlcv_produces_exact_bar_count(self) -> None:
        generator = SyntheticDataGenerator()
        frame = generator.generate_ohlcv("BTCUSDT", n_bars=250, seed=7)
        assert frame.height == 250

    def test_funding_rates_match_ohlcv_length(self) -> None:
        generator = SyntheticDataGenerator()
        ohlcv = generator.generate_ohlcv("BTCUSDT", n_bars=500, seed=3)
        funding = generator.generate_funding_rates(n_bars=500, seed=3)
        assert ohlcv.height == funding.height


class TestBitgetHistoricalFetcher:
    """Mocked Bitget fetcher behaviour."""

    @pytest.mark.asyncio
    async def test_http_429_retries_with_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetcher = BitgetHistoricalFetcher()
        responses = [
            httpx.Response(429, request=httpx.Request("GET", "https://api.bitget.com/test")),
            httpx.Response(
                200,
                json={"code": "00000", "data": []},
                request=httpx.Request("GET", "https://api.bitget.com/test"),
            ),
        ]

        async def fake_get(*args: object, **kwargs: object) -> httpx.Response:
            return responses.pop(0)

        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        fetcher._client.get = AsyncMock(side_effect=fake_get)  # type: ignore[method-assign]
        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(BitgetNoDataError):
            await fetcher.fetch_ohlcv(
                "BTCUSDT",
                "1h",
                "2024-01-01",
                "2024-01-02",
            )

        assert 1.0 in sleep_calls
        await fetcher.close()
