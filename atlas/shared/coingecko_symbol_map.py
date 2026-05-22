"""CoinGecko ``/simple/price`` ids and GeckoTerminal pool refs per ATLAS base symbol.

``ids`` must be CoinGecko API slugs (e.g. ``bitcoin``), not exchange tickers.
GeckoTerminal keys are ``{network_id}/{checksummed_or_lower_contract}`` as used by
``GET /networks/{network}/tokens/{address}/pools``.
"""

from __future__ import annotations

# BASE (e.g. BTC from BTC/USDT) → CoinGecko coin id for /simple/price and /coins/{id}
COINGECKO_SIMPLE_PRICE_ID_BY_BASE: dict[str, str] = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "DOT": "polkadot",
    "LINK": "chainlink",
    "TRX": "tron",
    "LTC": "litecoin",
    "ATOM": "cosmos",
    "NEAR": "near",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "MATIC": "matic-network",
    "SUI": "sui",
    "HYPE": "hyperliquid",
    "HBAR": "hedera-hashgraph",
    "TAO": "bittensor",
    "XAUT": "tether-gold",
    "UNI": "uniswap",
    "PAXG": "pax-gold",
    "WLFI": "world-liberty-financial",
    "ONDO": "ondo-finance",
    "ASTER": "aster-2",
    "SKY": "sky",
    "ICP": "internet-computer",
    "ETC": "ethereum-classic",
    "AAVE": "aave",
    "TON": "the-open-network",
    "XLM": "stellar",
    "XMR": "monero",
    "ZEC": "zcash",
    "BCH": "bitcoin-cash",
    "CC": "canton",
}

# DeFi / rotation bases → GeckoTerminal ``network/token`` path segment (see adapter)
GECKO_TERMINAL_TOKEN_REF_BY_BASE: dict[str, str] = {
    "UNI": "eth/0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
    "AAVE": "eth/0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
    "LINK": "eth/0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "CRV": "eth/0xD533a949740bb3306d119CC777fa900bA034cd52",
    "LDO": "eth/0x5A98FcBEA516Cf06857215779Fd812CA3beF1B32",
    "MKR": "eth/0x9f8f72aa9304c8b593d555f12ef6589cc3a579a2",
    "SNX": "eth/0xc011a73ee8576fb46f5e1c5751ca3b9fe0af2a6f",
    "COMP": "eth/0xc00e94cb662c3520282e6f5717214004a7f26888",
    "SUSHI": "eth/0x6b3595068778dd592e39a122f4f5a5cf09c90fe2",
    "INJ": "ethereum/0xe28b3b32b6c2a969866005f0dd84f69fcde564b8",
}
