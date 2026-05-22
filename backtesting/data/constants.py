"""Backtesting constants — asset universe and Bitget symbol mapping."""

from __future__ import annotations

# 33 POLARIS universe assets: 18 ALWAYS_ON + 15 top ROTATION (May 2026 snapshot).
POLARIS_UNIVERSE_ASSETS: tuple[str, ...] = (
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "SOLUSDT",
    "TRXUSDT",
    "DOGEUSDT",
    "HYPEUSDT",
    "ADAUSDT",
    "ZECUSDT",
    "BCHUSDT",
    "LINKUSDT",
    "XMRUSDT",
    "CCUSDT",
    "TONUSDT",
    "XLMUSDT",
    "LTCUSDT",
    "SUIUSDT",
    "AVAXUSDT",
    "HBARUSDT",
    "TAOUSDT",
    "XAUTUSDT",
    "UNIUSDT",
    "DOTUSDT",
    "PAXGUSDT",
    "WLFIUSDT",
    "NEARUSDT",
    "ONDOUSDT",
    "ASTERUSDT",
    "SKYUSDT",
    "ICPUSDT",
    "ETCUSDT",
    "AAVEUSDT",
)

SYMBOL_MAP: dict[str, str] = {
    asset: f"{asset}_UMCBL" for asset in POLARIS_UNIVERSE_ASSETS
}

TIMEFRAME_TO_BITGET: dict[str, str] = {
    "1h": "1H",
    "4h": "4H",
    "1d": "1D",
}

DEFAULT_DB_FILENAME: str = "polaris_backtest.duckdb"
