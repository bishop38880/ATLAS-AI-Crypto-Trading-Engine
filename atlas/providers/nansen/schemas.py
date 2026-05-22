"""Nansen ecosystem payload schemas — used by WhaleAgent for Solana/JUP extensions.

These are msgspec Structs (frozen) carrying external flow telemetry
that the WhaleAgent evaluates alongside Nansen snapshot data.

Sentinel v3.0 invariants:
  - All USD fields are Decimal.
  - Structs are immutable.
"""

from decimal import Decimal

import msgspec


class LSTFlowPayload(msgspec.Struct, frozen=True):
    """Solana Liquid Staking Token flow data for a single contract."""

    asset: str
    contract_name: str
    netflow_24h: Decimal = Decimal("0")
    netflow_7d: Decimal = Decimal("0")
    timestamp: int = 0


class RetailFlowPayload(msgspec.Struct, frozen=True):
    """Aggregate retail flow data for divergence detection."""

    netflow_usd: Decimal = Decimal("0")
    buy_pressure_ratio: float = 1.0


class BridgeFlowPayload(msgspec.Struct, frozen=True):
    """Cross-chain bridge flow data for capital rotation detection."""

    bridge_name: str = ""
    net_inflow_usd: Decimal = Decimal("0")
    target_chain: str = ""
