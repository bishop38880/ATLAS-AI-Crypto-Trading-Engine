"""Nansen MCP server data models — msgspec structs for high performance.

Adheres to Sentinel v3.0 architectural invariants.
"""

from decimal import Decimal
from typing import Any
import msgspec


class SmartMoneyFlow(msgspec.Struct, frozen=True):
    """Net USD flow from smart money wallets."""
    asset: str
    chain: str
    net_flow_usd: Decimal
    unique_smart_wallets: int
    time_range: str


class ExchangeNetflow(msgspec.Struct, frozen=True):
    """Net token movement to/from centralised exchanges."""
    asset: str
    netflow_usd: Decimal
    inflow_usd: Decimal
    outflow_usd: Decimal


class SmartMoneyHolder(msgspec.Struct, frozen=True):
    """Top smart money holders and their position changes."""
    wallet_address: str
    label: str
    balance_token: Decimal
    balance_usd: Decimal


class WalletProfile(msgspec.Struct, frozen=True):
    """Profile of a specific wallet address."""
    address: str
    labels: list[str]
    total_value_usd: Decimal
    primary_chain: str


def encode_model(model: Any) -> str:
    """Serialize model to JSON using msgspec."""
    return msgspec.json.encode(model).decode("utf-8")
