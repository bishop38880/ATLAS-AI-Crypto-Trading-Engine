"""Nansen MCP tool names and chain identifiers used by ATLAS."""

# Nansen MCP tool names used by ATLAS
NANSEN_TOOLS = {
    # Token-level smart money flows
    "get_token_smart_money_flow": {
        "description": "Net USD flow from smart money wallets for a token",
        "params": ["token_address", "chain", "time_range"],  # time_range: "24h" | "7d"
    },
    # Wallet labelling
    "get_wallet_labels": {
        "description": "Labels for a wallet address (Exchange, Smart Money, etc.)",
        "params": ["wallet_address", "chain"],
    },
    # Exchange netflow
    "get_exchange_netflow": {
        "description": "Net token flow to/from centralised exchanges",
        "params": ["token_address", "chain", "time_range"],
    },
    # Smart money top holders
    "get_smart_money_top_holders": {
        "description": "Top smart money holders and their position changes",
        "params": ["token_address", "chain", "limit"],
    },
    # Wallet portfolio
    "get_wallet_portfolio": {
        "description": "Current holdings for a tracked whale wallet",
        "params": ["wallet_address", "chain"],
    },
}

# Chain identifiers expected by Nansen MCP
NANSEN_CHAINS: dict[str, str] = {
    "ETH": "ethereum",
    "BNB": "bsc",
    "AVAX": "avalanche",
    "MATIC": "polygon",
    "ARB": "arbitrum",
    "OP": "optimism",
    "SOL": "solana",
}

# Token contract addresses for tracked assets (Ethereum mainnet primary)
# Fallback: if no contract address known, Nansen MCP accepts symbol for major assets
TOKEN_ADDRESSES: dict[str, str] = {
    "ETH": "0x0000000000000000000000000000000000000000",  # native
    "BTC": "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599",  # WBTC as proxy for flows
    "LINK": "0x514910771af9ca656af840dff83e8264ecf986ca",
    "UNI": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
}
