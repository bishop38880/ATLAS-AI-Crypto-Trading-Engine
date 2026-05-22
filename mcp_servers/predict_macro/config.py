"""
FRED series identifiers and Ethereum stablecoin treasury configuration.

MacroCrossMarketAgent - Fiat Gravity Engine baseline layer.
Central bank liquidity (FRED) and on-chain fiat proxies (USDT/USDC mint-burn).
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# FRED series IDs (Federal Reserve Economic Data)
# -----------------------------------------------------------------------------
FRED_SERIES_DXY: str = "DTWEXBGS"  # Trade-weighted USD index (broad)
FRED_SERIES_US10Y: str = "DGS10"  # 10-year Treasury constant maturity
FRED_SERIES_SOFR: str = "SOFR"
FRED_SERIES_M2: str = "WM2NS"  # M2, not seasonally adjusted (weekly)

FRED_SERIES_IDS: tuple[str, ...] = (
    FRED_SERIES_DXY,
    FRED_SERIES_US10Y,
    FRED_SERIES_SOFR,
    FRED_SERIES_M2,
)

FRED_OBSERVATIONS_URL: str = "https://api.stlouisfed.org/fred/series/observations"

# -----------------------------------------------------------------------------
# Ethereum mainnet — USDT / USDC (6 decimals on Ethereum)
# -----------------------------------------------------------------------------
USDT_CONTRACT_ADDRESS: str = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
USDC_CONTRACT_ADDRESS: str = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"

STABLECOIN_DECIMALS_ETHEREUM: int = 6

# keccak256("Transfer(address,address,uint256)")
TRANSFER_EVENT_TOPIC: str = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

ZERO_ADDRESS: str = "0x0000000000000000000000000000000000000000"
ZERO_ADDRESS_TOPIC: str = (
    "0x0000000000000000000000000000000000000000000000000000000000000000"
)

# -----------------------------------------------------------------------------
# ERC-20 Transfer event minimal ABI (mint/burn via zero address)
# -----------------------------------------------------------------------------
TRANSFER_EVENT_ABI: list[dict[str, object]] = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "from", "type": "address"},
            {"indexed": True, "name": "to", "type": "address"},
            {"indexed": False, "name": "value", "type": "uint256"},
        ],
        "name": "Transfer",
        "type": "event",
    },
]

# -----------------------------------------------------------------------------
# Background loop tuning
# -----------------------------------------------------------------------------
TRADFI_POLL_INTERVAL_SECONDS: int = 43_200  # 12 hours
WEB3_POLL_INTERVAL_SECONDS: int = 600  # 10 minutes
FRED_LOOKBACK_DAYS: int = 90
BLOCK_CHUNK_SIZE: int = 2000
LOOKBACK_BLOCKS_30D: int = 216_000  # ~30d at 12s/block
ETH_GETLOGS_TIMEOUT_SECONDS: float = 20.0
HTTP_TIMEOUT_SECONDS: float = 45.0
