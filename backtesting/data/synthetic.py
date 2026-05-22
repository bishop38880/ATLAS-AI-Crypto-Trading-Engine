"""GBM + Markov regime synthetic data generator for CI and tutorial mode."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

_REGIMES: tuple[str, ...] = ("bull", "bear", "volatile")
_TRANSITION_MATRIX: np.ndarray = np.array([
    [0.92, 0.05, 0.03],
    [0.05, 0.90, 0.05],
    [0.08, 0.08, 0.84],
])
_BASE_PRICES: dict[str, float] = {
    "BTCUSDT": 50_000.0,
    "ETHUSDT": 3_000.0,
    "DEFAULT": 100.0,
}


class SyntheticDataGenerator:
    """Generate synthetic OHLCV and funding data with Markov regime shifts."""

    def generate_ohlcv(
        self,
        asset: str,
        n_bars: int = 1000,
        timeframe: str = "1h",
        seed: int | None = None,
        include_regime_labels: bool = False,
    ) -> pl.DataFrame:
        """Generate synthetic OHLCV using GBM paths and Markov regimes."""
        rng = np.random.default_rng(seed)
        regimes = _simulate_regimes(n_bars, rng)
        closes = _simulate_gbm_closes(asset, n_bars, regimes, rng)
        timestamps = _build_timestamps(n_bars, timeframe)
        frame = _build_ohlcv_frame(timestamps, closes, rng)
        if include_regime_labels:
            frame = frame.with_columns(pl.Series("regime", regimes.tolist()))
        return frame

    def generate_funding_rates(
        self,
        n_bars: int = 1000,
        seed: int | None = None,
    ) -> pl.DataFrame:
        """Generate synthetic funding rates correlated with price direction."""
        rng = np.random.default_rng(seed)
        regimes = _simulate_regimes(n_bars, rng)
        rates = _simulate_funding_rates(regimes, rng)
        timestamps = _build_timestamps(n_bars, "1h")
        annualised = rates * 3.0 * 365.0
        return pl.DataFrame({
            "timestamp_utc": timestamps,
            "funding_rate": rates.tolist(),
            "funding_annualised": annualised.tolist(),
        })


def _simulate_regimes(n_bars: int, rng: np.random.Generator) -> np.ndarray:
    states = np.zeros(n_bars, dtype=int)
    for index in range(1, n_bars):
        previous = states[index - 1]
        states[index] = rng.choice(3, p=_TRANSITION_MATRIX[previous])
    return np.array([_REGIMES[state] for state in states], dtype=object)


def _simulate_gbm_closes(
    asset: str,
    n_bars: int,
    regimes: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    base_price = _BASE_PRICES.get(asset.upper(), _BASE_PRICES["DEFAULT"])
    closes = np.empty(n_bars, dtype=float)
    closes[0] = base_price
    for index in range(1, n_bars):
        drift, volatility = _regime_params(str(regimes[index]))
        shock = rng.normal(drift, volatility)
        closes[index] = max(closes[index - 1] * np.exp(shock), 0.01)
    return closes


def _regime_params(regime: str) -> tuple[float, float]:
    if regime == "bull":
        return 0.0004, 0.008
    if regime == "bear":
        return -0.0004, 0.010
    return 0.0, 0.020


def _build_timestamps(n_bars: int, timeframe: str) -> list[str]:
    step_hours = {"1h": 1, "4h": 4, "1d": 24}[timeframe]
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return [
        (start + timedelta(hours=step_hours * index)).isoformat()
        for index in range(n_bars)
    ]


def _build_ohlcv_frame(
    timestamps: list[str],
    closes: np.ndarray,
    rng: np.random.Generator,
) -> pl.DataFrame:
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    wick = rng.uniform(0.001, 0.01, size=len(closes))
    highs = closes * (1.0 + wick)
    lows = closes * (1.0 - wick)
    volumes = rng.uniform(10.0, 500.0, size=len(closes))
    volume_usd = volumes * closes
    return pl.DataFrame({
        "timestamp_utc": timestamps,
        "open": opens.tolist(),
        "high": highs.tolist(),
        "low": lows.tolist(),
        "close": closes.tolist(),
        "volume": volumes.tolist(),
        "volume_usd": volume_usd.tolist(),
    })


def _simulate_funding_rates(
    regimes: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    rates = np.zeros(len(regimes), dtype=float)
    for index, regime in enumerate(regimes):
        if regime == "bull":
            rates[index] = abs(rng.normal(0.00015, 0.00005))
        elif regime == "bear":
            rates[index] = -abs(rng.normal(0.00015, 0.00005))
        else:
            rates[index] = rng.normal(0.0, 0.0002)
    return rates
