"""Simplified confluence scoring from historical bar data."""

from __future__ import annotations

import math
from decimal import Decimal

from pydantic import BaseModel, Field

from backtesting.data.models import FundingRateBar, OHLCVBar
from backtesting.engine.config import ScoreThresholds


class BarScorerInput(BaseModel, frozen=True):
    """All data visible at bar T for scoring — strictly no lookahead."""

    asset: str
    bar_index: int
    current_bar: OHLCVBar
    lookback_ohlcv: list[OHLCVBar]
    lookback_funding: list[FundingRateBar]
    btc_close_series: list[Decimal]
    higher_tf_bars: list[OHLCVBar]
    live_signal_bonus: int = 0
    thresholds: ScoreThresholds = Field(default_factory=ScoreThresholds)


class BarScore(BaseModel, frozen=True):
    """Score output for a single bar."""

    total: int
    derivatives: int
    technical: int
    market_context: int
    direction: str
    signal_class: str
    factors: list[str]
    atr: Decimal = Decimal("0")


class BarScorer:
    """Simplified confluence scorer operating on historical bar data."""

    def score(self, inp: BarScorerInput) -> BarScore:
        """Score a single bar. Pure function — no side effects, no I/O."""
        deriv_pts, deriv_dir, deriv_factors = _score_derivatives(inp)
        tech_pts, tech_dir, tech_factors = _score_technical(inp)
        ctx_pts, ctx_factors, chaotic = _score_market_context(inp)
        total = deriv_pts + tech_pts + ctx_pts + inp.live_signal_bonus
        if chaotic:
            total = int(total * 0.7)
        direction = _resolve_direction(deriv_dir, tech_dir)
        signal_class = _classify_signal(total, inp.thresholds)
        factors = deriv_factors + tech_factors + ctx_factors
        atr = _calculate_atr(_closes_from_bars(inp.lookback_ohlcv + [inp.current_bar]))
        return BarScore(
            total=total,
            derivatives=deriv_pts,
            technical=tech_pts,
            market_context=ctx_pts,
            direction=direction,
            signal_class=signal_class,
            factors=factors,
            atr=atr,
        )


def _closes_from_bars(bars: list[OHLCVBar]) -> list[float]:
    return [float(bar.close) for bar in bars]


def _volumes_from_bars(bars: list[OHLCVBar]) -> list[float]:
    return [float(bar.volume) for bar in bars]


def _classify_signal(total: int, thresholds: ScoreThresholds) -> str:
    if total >= thresholds.strong:
        return "STRONG"
    if total >= thresholds.buy:
        return "BUY"
    if total >= thresholds.weak:
        return "WEAK"
    return "NO_TRADE"


def _resolve_direction(*directions: str) -> str:
    long_votes = sum(1 for direction in directions if direction == "LONG")
    short_votes = sum(1 for direction in directions if direction == "SHORT")
    if long_votes > short_votes and long_votes > 0:
        return "LONG"
    if short_votes > long_votes and short_votes > 0:
        return "SHORT"
    return "NEUTRAL"


def _score_derivatives(inp: BarScorerInput) -> tuple[int, str, list[str]]:
    factors: list[str] = []
    direction = "NEUTRAL"
    funding_pts = 0
    volume_pts = 0
    reset_pts = 0
    if inp.lookback_funding:
        funding_pts, funding_dir = _score_funding_z(inp.lookback_funding)
        if funding_dir != "NEUTRAL":
            direction = funding_dir
        factors.append(f"funding_z={funding_pts}")
        reset_pts, reset_dir = _score_funding_reset(inp.lookback_funding)
        if reset_dir == "LONG":
            direction = "LONG"
        if reset_pts:
            factors.append(f"funding_reset={reset_pts}")
    volume_pts = _score_volume_surge(inp.lookback_ohlcv, inp.current_bar)
    factors.append(f"volume_surge={volume_pts}")
    total = min(funding_pts + volume_pts + reset_pts, 75)
    return total, direction, factors


def _score_funding_z(rates: list[FundingRateBar]) -> tuple[int, str]:
    values = [float(rate.funding_rate) for rate in rates[-30:]]
    if len(values) < 2:
        return 0, "NEUTRAL"
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0.0:
        return 0, "NEUTRAL"
    z_score = (values[-1] - mean) / std
    if abs(z_score) >= 2.5:
        direction = "LONG" if z_score < 0 else "SHORT"
        return 40, direction
    if abs(z_score) >= 1.5:
        direction = "LONG" if z_score < 0 else "SHORT"
        return 20, direction
    return 0, "NEUTRAL"


def _score_funding_reset(rates: list[FundingRateBar]) -> tuple[int, str]:
    recent = [float(rate.funding_rate) for rate in rates[-4:]]
    if len(recent) < 2:
        return 0, "NEUTRAL"
    for index in range(1, min(4, len(recent))):
        previous = recent[-index - 1]
        current = recent[-index]
        if previous < -0.5 and current > -0.1:
            return 15, "LONG"
    return 0, "NEUTRAL"


def _score_volume_surge(lookback: list[OHLCVBar], current: OHLCVBar) -> int:
    if len(lookback) < 20:
        return 0
    recent_volumes = _volumes_from_bars(lookback[-20:])
    mean_volume = sum(recent_volumes) / len(recent_volumes)
    if mean_volume <= 0:
        return 0
    ratio = float(current.volume) / mean_volume
    if ratio > 2.5:
        return 20
    if ratio > 1.5:
        return 12
    if ratio > 1.0:
        return 5
    return 0


def _score_technical(inp: BarScorerInput) -> tuple[int, str, list[str]]:
    bars = inp.lookback_ohlcv + [inp.current_bar]
    closes = _closes_from_bars(bars)
    factors: list[str] = []
    direction = "NEUTRAL"
    if len(closes) < 15:
        return 0, direction, factors
    rsi = _calculate_rsi(closes, 14)
    rsi_pts, rsi_dir = _score_rsi(rsi)
    factors.append(f"rsi={rsi_pts}")
    if rsi_dir != "NEUTRAL":
        direction = rsi_dir
    volume_pts = _score_volume_surge(inp.lookback_ohlcv, inp.current_bar)
    adx = _calculate_adx(bars, 14)
    adx_pts = _score_adx(adx)
    factors.append(f"adx={adx_pts}")
    bb_pts, bb_dir = _score_bollinger(closes)
    factors.append(f"bollinger={bb_pts}")
    if bb_dir != "NEUTRAL":
        direction = bb_dir
    macd_pts, macd_dir = _score_macd_cross(closes)
    factors.append(f"macd={macd_pts}")
    if macd_dir != "NEUTRAL":
        direction = macd_dir
    technical = rsi_pts + max(volume_pts, 0) + adx_pts + bb_pts + macd_pts
    technical = min(technical, 45)
    if adx < 20:
        technical = technical // 2
        factors.append("adx_chop_halved")
    return technical, direction, factors


def _score_rsi(rsi: float) -> tuple[int, str]:
    if rsi > 70:
        return 8, "SHORT"
    if rsi < 30:
        return 8, "LONG"
    if rsi >= 65 or rsi <= 35:
        return 4, "NEUTRAL"
    return 0, "NEUTRAL"


def _score_adx(adx: float) -> int:
    if adx > 30:
        return 8
    if adx > 20:
        return 4
    return 0


def _score_bollinger(closes: list[float]) -> tuple[int, str]:
    if len(closes) < 20:
        return 0, "NEUTRAL"
    window = closes[-20:]
    mean = sum(window) / len(window)
    variance = sum((value - mean) ** 2 for value in window) / len(window)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0.0:
        return 0, "NEUTRAL"
    price = closes[-1]
    z = (price - mean) / std
    if z >= 2.5:
        return 7, "SHORT"
    if z >= 2.0:
        return 4, "SHORT"
    if z <= -2.5:
        return 7, "LONG"
    if z <= -2.0:
        return 4, "LONG"
    return 0, "NEUTRAL"


def _score_macd_cross(closes: list[float]) -> tuple[int, str]:
    if len(closes) < 35:
        return 0, "NEUTRAL"
    histogram = _macd_histogram_series(closes)
    if len(histogram) < 2:
        return 0, "NEUTRAL"
    previous = histogram[-2]
    current = histogram[-1]
    if previous < 0 <= current:
        return 5, "LONG"
    if previous > 0 >= current:
        return 5, "SHORT"
    return 0, "NEUTRAL"


def _score_market_context(inp: BarScorerInput) -> tuple[int, list[str], bool]:
    factors: list[str] = []
    bars = inp.lookback_ohlcv + [inp.current_bar]
    closes = _closes_from_bars(bars)
    atr_values = _atr_series(bars)
    if len(atr_values) < 21:
        return 0, factors, False
    current_atr = atr_values[-1]
    mean_atr = sum(atr_values[-21:-1]) / 20
    ratio = current_atr / mean_atr if mean_atr > 0 else 1.0
    chaotic = ratio > 2.0
    vol_pts = _score_volatility_regime(ratio)
    factors.append(f"vol_regime={vol_pts}")
    corr_pts = _score_btc_correlation(inp.asset, closes, inp.btc_close_series)
    factors.append(f"btc_corr={corr_pts}")
    return min(vol_pts + corr_pts, 30), factors, chaotic


def _score_volatility_regime(ratio: float) -> int:
    if ratio < 0.5:
        return 12
    if ratio <= 1.0:
        return 8
    if ratio <= 2.0:
        return 5
    return 2


def _score_btc_correlation(
    asset: str,
    closes: list[float],
    btc_closes: list[Decimal],
) -> int:
    if asset == "BTCUSDT":
        return 10
    if len(closes) < 24 or len(btc_closes) < 24:
        return 0
    asset_tail = closes[-24:]
    btc_tail = [float(value) for value in btc_closes[-24:]]
    correlation = _pearson_correlation(asset_tail, btc_tail)
    if correlation < 0.4:
        return 15
    if correlation <= 0.7:
        return 8
    return 3


def _pearson_correlation(x_values: list[float], y_values: list[float]) -> float:
    count = min(len(x_values), len(y_values))
    if count < 2:
        return 0.0
    x_mean = sum(x_values[:count]) / count
    y_mean = sum(y_values[:count]) / count
    numerator = sum(
        (x_values[index] - x_mean) * (y_values[index] - y_mean)
        for index in range(count)
    )
    x_var = sum((value - x_mean) ** 2 for value in x_values[:count])
    y_var = sum((value - y_mean) ** 2 for value in y_values[:count])
    denominator = math.sqrt(x_var * y_var)
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


def _calculate_rsi(closes: list[float], period: int) -> float:
    if len(closes) <= period:
        return 50.0
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(closes)):
        delta = closes[index] - closes[index - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for index in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[index]) / period
        avg_loss = (avg_loss * (period - 1) + losses[index]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calculate_adx(bars: list[OHLCVBar], period: int) -> float:
    if len(bars) <= period + 1:
        return 0.0
    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(bars)):
        high = float(bars[index].high)
        low = float(bars[index].low)
        prev_close = float(bars[index - 1].close)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
        up_move = high - float(bars[index - 1].high)
        down_move = float(bars[index - 1].low) - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
    atr = sum(true_ranges[:period]) / period
    plus_di = 100 * sum(plus_dm[:period]) / period / atr if atr else 0.0
    minus_di = 100 * sum(minus_dm[:period]) / period / atr if atr else 0.0
    for index in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[index]) / period
        plus_di = (plus_di * (period - 1) + 100 * plus_dm[index] / atr) / period if atr else 0.0
        minus_di = (minus_di * (period - 1) + 100 * minus_dm[index] / atr) / period if atr else 0.0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if plus_di + minus_di else 0.0
    return dx


def _calculate_atr(closes: list[float]) -> Decimal:
    if len(closes) < 2:
        return Decimal("0")
    ranges = [abs(closes[index] - closes[index - 1]) for index in range(1, len(closes))]
    window = ranges[-14:] if len(ranges) >= 14 else ranges
    return Decimal(str(sum(window) / len(window)))


def _atr_series(bars: list[OHLCVBar]) -> list[float]:
    values: list[float] = []
    for index in range(1, len(bars)):
        high = float(bars[index].high)
        low = float(bars[index].low)
        prev_close = float(bars[index - 1].close)
        values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return values


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    multiplier = 2 / (period + 1)
    result = [values[0]]
    for index in range(1, len(values)):
        result.append(values[index] * multiplier + result[-1] * (1 - multiplier))
    return result


def _macd_histogram_series(closes: list[float]) -> list[float]:
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [fast - slow for fast, slow in zip(ema12, ema26)]
    signal = _ema(macd_line, 9)
    return [macd - sig for macd, sig in zip(macd_line, signal)]
