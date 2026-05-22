"""
EVM chain ID mapping for GoPlus and Tenderly.

PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
Maps human-readable EVM chain names to their numeric chain IDs
for GoPlus API queries and Tenderly network slugs.

Sentinel Invariants:
  - No mutable global state
  - No os.getenv (config loaded in server.py via dotenv)
  - Max 40 lines per function
"""

from __future__ import annotations


# ──────────────────────────────────────────────────────────────
# Chain name → numeric chain ID (GoPlus / on-chain standard)
# ──────────────────────────────────────────────────────────────

CHAIN_NAME_TO_ID: dict[str, str] = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "base": "8453",
    "avalanche": "43114",
    "fantom": "250",
    "cronos": "25",
    "gnosis": "100",
    "linea": "59144",
    "scroll": "534352",
    "zksync": "324",
    "blast": "81457",
}

# ──────────────────────────────────────────────────────────────
# Chain name → Tenderly network slug
# ──────────────────────────────────────────────────────────────

CHAIN_NAME_TO_TENDERLY_NETWORK: dict[str, str] = {
    "ethereum": "1",
    "bsc": "56",
    "polygon": "137",
    "arbitrum": "42161",
    "optimism": "10",
    "base": "8453",
    "avalanche": "43114",
    "fantom": "250",
    "cronos": "25",
    "gnosis": "100",
    "linea": "59144",
    "scroll": "534352",
    "zksync": "324",
    "blast": "81457",
}


def resolve_chain_id(chain_name_or_id: str) -> str:
    """
    Resolve a chain name or numeric ID to a numeric chain ID string.

    PROMETHEUS Execution Router — Pre-Execution Security Guardrail.
    Accepts either a human-readable name ("ethereum") or a raw
    numeric string ("1") and returns the canonical chain ID.

    Args:
        chain_name_or_id: Chain name (e.g. "ethereum") or ID ("1").

    Returns:
        Numeric chain ID string (e.g. "1").

    Raises:
        ValueError: If the chain is not recognized.
    """
    lower: str = chain_name_or_id.lower().strip()
    if lower in CHAIN_NAME_TO_ID:
        return CHAIN_NAME_TO_ID[lower]
    if lower.isdigit():
        return lower
    raise ValueError(f"Unknown EVM chain: {chain_name_or_id}")


def resolve_tenderly_network(chain_name_or_id: str) -> str:
    """
    Resolve a chain name or ID to a Tenderly network identifier.

    Args:
        chain_name_or_id: Chain name (e.g. "base") or ID ("8453").

    Returns:
        Tenderly network ID string.

    Raises:
        ValueError: If the chain is not recognized.
    """
    lower: str = chain_name_or_id.lower().strip()
    if lower in CHAIN_NAME_TO_TENDERLY_NETWORK:
        return CHAIN_NAME_TO_TENDERLY_NETWORK[lower]
    if lower.isdigit():
        return lower
    raise ValueError(f"Unknown Tenderly network: {chain_name_or_id}")
