"""DeFi Llama MCP models."""

from decimal import Decimal

import msgspec


class TVLSnapshot(msgspec.Struct, frozen=True):
    """TVL snapshot for a specific protocol."""
    protocol: str
    chain: str
    tvl_usd: Decimal
    tvl_change_1d_pct: Decimal
    tvl_change_7d_pct: Decimal
    category: str
    fetched_at_ms: int


class ChainTVL(msgspec.Struct, frozen=True):
    """TVL for a blockchain chain."""
    chain: str
    tvl_usd: Decimal
    tvl_change_1d_pct: Decimal
    fetched_at_ms: int


class StablecoinSupply(msgspec.Struct, frozen=True):
    """Stablecoin supply metrics."""
    total_mcap_usd: Decimal
    usdt_mcap_usd: Decimal
    usdc_mcap_usd: Decimal
    usdt_dominance_pct: Decimal
    fetched_at_ms: int


class YieldPool(msgspec.Struct, frozen=True):
    """Yield pool data."""
    pool_id: str
    project: str
    chain: str
    symbol: str
    apy: Decimal
    tvl_usd: Decimal
    fetched_at_ms: int


class DefiLlamaContext(msgspec.Struct, frozen=True):
    """Aggregated context from DeFi Llama."""
    chain_tvl: ChainTVL | None = None
    protocol_tvls: list[TVLSnapshot] = msgspec.field(default_factory=list)
    stablecoin_supply: StablecoinSupply | None = None
    yield_pools: list[YieldPool] = msgspec.field(default_factory=list)
    fetched_at_ms: int = 0
