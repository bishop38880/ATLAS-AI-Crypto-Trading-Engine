"""
Contract addresses, ABIs, and constants for RWA Proof of Reserve monitoring.

Section 5 Architecture: Macro Context — Institutional Rotation.
Maps RWA symbols to their ERC-20 contract addresses, on-chain decimals,
and corresponding Chainlink Proof of Reserve oracle aggregator addresses.

All ABI fragments are minimal — only the functions/events required
for PoR health checks and mint/burn flow detection.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# ERC-20 Transfer event topic (keccak256 of "Transfer(address,address,uint256)")
# ---------------------------------------------------------------------------
TRANSFER_EVENT_TOPIC: str = (
    "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
)

# ---------------------------------------------------------------------------
# Zero address — used to identify mint (from=0x0) and burn (to=0x0) events
# ---------------------------------------------------------------------------
ZERO_ADDRESS: str = "0x0000000000000000000000000000000000000000"
ZERO_ADDRESS_PADDED: str = (
    "0x0000000000000000000000000000000000000000000000000000000000000000"
)

# ---------------------------------------------------------------------------
# Supported RWA contract registry
# Keys are canonical symbols used by the MarketRegimeAgent.
# ---------------------------------------------------------------------------
RWA_CONTRACTS: dict[str, dict[str, str | int]] = {
    "BUIDL": {
        "name": "BlackRock USD Institutional Digital Liquidity Fund",
        "token_address": "0x7712c34205737192402172409a8F7ccef8aA2AEc",
        "por_oracle_address": "0x1B28388bF0aaeCe0e1F89fC924f660B0bE0b8705",
        "chain": "ethereum",
        "token_decimals": 6,
        "oracle_decimals": 18,
    },
    "USDY": {
        "name": "Ondo US Dollar Yield Token",
        "token_address": "0x96F6eF951840721AdBF46Ac996b59E0235CB985C",
        "por_oracle_address": "0x4c07DF05C898d5C222e20b87e5F4507801aA1F22",
        "chain": "ethereum",
        "token_decimals": 18,
        "oracle_decimals": 18,
    },
    "OUSG": {
        "name": "Ondo Short-Term US Government Bond Fund",
        "token_address": "0x1B19C19393e2d034D8Ff31ff34c81252FcBbee92",
        "por_oracle_address": "0x0000000000000000000000000000000000000000",
        "chain": "ethereum",
        "token_decimals": 18,
        "oracle_decimals": 18,
    },
}

# ---------------------------------------------------------------------------
# Supported symbols set for quick validation
# ---------------------------------------------------------------------------
SUPPORTED_SYMBOLS: frozenset[str] = frozenset(RWA_CONTRACTS.keys())

# ---------------------------------------------------------------------------
# Minimal ABI fragments — ERC-20 read functions + Transfer event
# ---------------------------------------------------------------------------
ERC20_ABI: list[dict] = [
    {
        "constant": True,
        "inputs": [],
        "name": "totalSupply",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
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

# ---------------------------------------------------------------------------
# Minimal ABI — Chainlink AggregatorV3Interface
# ---------------------------------------------------------------------------
AGGREGATOR_V3_ABI: list[dict] = [
    {
        "constant": True,
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]

# ---------------------------------------------------------------------------
# Poller configuration constants
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS: int = 180  # 3 minutes between scans
BLOCK_CHUNK_SIZE: int = 2000  # Max blocks per eth_getLogs call
LOOKBACK_BLOCKS_24H: int = 7200  # ~24h at 12s/block
LOOKBACK_BLOCKS_7D: int = 50_400  # ~7d at 12s/block

# ---------------------------------------------------------------------------
# Rotation signal thresholds
# ---------------------------------------------------------------------------
ROTATION_VELOCITY_THRESHOLD: float = 3.0  # 24h flow > 3x 7d avg → risk_off
UNDERCOLLATERALIZATION_THRESHOLD: str = "1.0"  # Ratio below 1.0 = under-backed
