"""Known Solana addresses for POLARIS whale/exchange flow tracking.

Sources: Solscan labeled addresses, Helius address labels API, public entity lists.
Exchange hot wallets rotate periodically — verify every 1–2 months.
Last verified: May 2026.
"""

from __future__ import annotations

# Exchange deposit/hot wallet addresses (SOL to exchange = inflow / selling pressure)
EXCHANGE_ADDRESSES: dict[str, list[str]] = {
    "binance": [
        "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9",
        "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM",
    ],
    "coinbase": [
        "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS",
        "2AQdpHJ2JpcEgPiATUXjQxA8QmafFegfQwSLWSprPicm",
    ],
    "kraken": [
        "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5",
    ],
    "okx": [
        "5VCwKtCXgCDuQosEQR6mG5oP6E6ckMapjL59RkHXqSzy",
    ],
    "bybit": [
        "AC5RDfQFmDS1deWZos921JfqscXdByf4BKHs5ACWjtW2",
    ],
}

ALL_EXCHANGE_ADDRESSES: set[str] = set()
for _addrs in EXCHANGE_ADDRESSES.values():
    ALL_EXCHANGE_ADDRESSES.update(_addrs)

ADDRESS_TO_EXCHANGE: dict[str, str] = {}
for _exchange, _addrs in EXCHANGE_ADDRESSES.items():
    for _addr in _addrs:
        ADDRESS_TO_EXCHANGE[_addr] = _exchange

WHALE_WALLETS: dict[str, str] = {}

PROGRAM_ADDRESSES: dict[str, str] = {
    "jupiter_aggregator_v6": "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    "raydium_amm": "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",
    "marinade_finance": "MarBmsSgKXdrN1egZf5sqe1TMai9K1rChYNDJgjq7aD",
    "orca_whirlpool": "whirLbMiicVdio4qvUfM5KAg6Ct8VwpYzGff3uctyCc",
    "tensor_swap": "TSWAPaqyCSx2KABk68Shruf4rp7CxcNi8hAsbdwmHbN",
}

JUP_TOKEN_MINT: str = "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN"

WHALE_TX_THRESHOLD_USD: float = 50_000.0
LARGE_TX_THRESHOLD_USD: float = 500_000.0
WEBHOOK_MIN_SOL: float = 100.0

HELIUS_ENABLED_SYMBOLS: frozenset[str] = frozenset({"SOL", "JUP"})
