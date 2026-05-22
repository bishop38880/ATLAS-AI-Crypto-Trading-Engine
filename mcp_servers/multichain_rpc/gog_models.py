"""Universal Graph-of-Graphs (GoG) schema for cross-chain normalization.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.

Abstracts away the fundamental UTXO (Bitcoin) vs Account-state (Ethereum)
paradigm mismatch into a unified directed graph model.  Every on-chain
entity becomes a ``GoGNode`` and every capital movement becomes a ``GoGEdge``.
The ``OnChainIntelligenceAgent`` consumes these normalized structures to
detect cross-chain liquidity rotation without chain-specific awareness.

All financial values use ``decimal.Decimal`` — ``float`` is strictly
banned for any value representing money, gas, or token amounts.
Serialization uses ``msgspec`` with custom Decimal hooks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

import msgspec
from pydantic import BaseModel, Field


# ─────────────────────── Decimal Hooks for msgspec ───────────────────────────


def decimal_enc_hook(obj: Any) -> Any:
    """Encode ``Decimal`` as a JSON string to preserve precision.

    Called by ``msgspec.json.Encoder`` for types it does not handle
    natively.  Returns the string representation so the downstream
    consumer can reconstruct the exact ``Decimal`` value.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Unsupported type: {type(obj)}")


def decimal_dec_hook(type_: type, obj: Any) -> Any:
    """Decode a JSON string or number into ``Decimal``.

    Handles both ``"123.456"`` (string) and ``123.456`` (float/int)
    inputs that may arrive from Etherscan or Bitcoin Core payloads.
    """
    if type_ is Decimal:
        return Decimal(str(obj))
    raise TypeError(f"Unsupported type: {type_}")


# Pre-built encoder / decoder singletons — reuse everywhere.
ENCODER = msgspec.json.Encoder(enc_hook=decimal_enc_hook)
DECODER = msgspec.json.Decoder(dec_hook=decimal_dec_hook)


# ─────────────────────── Enums ───────────────────────────────────────────────


class ChainId(str, Enum):
    """Canonical chain identifiers for the GoG super-layer."""

    ETHEREUM = "ethereum"
    BITCOIN = "bitcoin"
    SOLANA = "solana"


class NodeType(str, Enum):
    """Classification of graph nodes."""

    WALLET = "wallet"
    CONTRACT = "contract"
    CEX_HOT_WALLET = "cex_hot_wallet"
    BRIDGE_CONTRACT = "bridge_contract"
    UNKNOWN = "unknown"


class EdgeType(str, Enum):
    """Classification of graph edges."""

    TRANSFER = "transfer"
    INTERNAL_TRANSFER = "internal_transfer"
    ERC20_TRANSFER = "erc20_transfer"
    LIQUIDATION = "liquidation"
    BRIDGE_DEPOSIT = "bridge_deposit"
    BRIDGE_WITHDRAWAL = "bridge_withdrawal"
    UTXO_SPEND = "utxo_spend"


# ─────────────────────── GoG Node ────────────────────────────────────────────


class GoGNode(BaseModel, frozen=True):
    """A single entity in the Graph-of-Graphs super-layer.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Represents a wallet, contract, CEX hot wallet, or bridge contract
    normalized across Bitcoin and Ethereum address spaces.

    Attributes:
        node_id: Deterministic identifier (chain:address).
        address: Raw on-chain address (hex for EVM, base58/bech32 for BTC).
        chain: Source blockchain.
        node_type: Classification of this entity.
        label: Human-readable label (e.g. 'Binance Hot Wallet 1').
        first_seen: Earliest observed activity timestamp.
        last_seen: Latest observed activity timestamp.
        total_inflow: Cumulative inbound value (Decimal).
        total_outflow: Cumulative outbound value (Decimal).
    """

    node_id: str = Field(
        description="Deterministic ID: '{chain}:{address_lower}'.",
    )
    address: str = Field(
        description="Raw on-chain address.",
    )
    chain: ChainId = Field(
        description="Source blockchain identifier.",
    )
    node_type: NodeType = Field(
        default=NodeType.UNKNOWN,
        description="Entity classification.",
    )
    label: str = Field(
        default="",
        description="Human-readable label if identified.",
    )
    first_seen: str = Field(
        default="",
        description="ISO-8601 timestamp of earliest activity.",
    )
    last_seen: str = Field(
        default="",
        description="ISO-8601 timestamp of latest activity.",
    )
    total_inflow: Decimal = Field(
        default=Decimal("0"),
        description="Cumulative inbound value in native units.",
    )
    total_outflow: Decimal = Field(
        default=Decimal("0"),
        description="Cumulative outbound value in native units.",
    )


# ─────────────────────── GoG Edge ────────────────────────────────────────────


class GoGEdge(BaseModel, frozen=True):
    """A directed edge in the Graph-of-Graphs super-layer.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Represents a single capital movement between two GoG nodes.
    All values are in the native denomination of the source chain
    (wei → ETH, satoshi → BTC) converted to human-readable units
    using ``Decimal`` arithmetic.

    Attributes:
        edge_id: Deterministic identifier (txhash:log_index or txid:vout).
        from_node_id: Source GoGNode.node_id.
        to_node_id: Destination GoGNode.node_id.
        chain: Source blockchain.
        edge_type: Classification of this transfer.
        value: Transfer amount in human-readable native units (Decimal).
        value_usd: Estimated USD value at time of transfer (Decimal).
        tx_hash: On-chain transaction hash.
        block_number: Block height of the transaction.
        timestamp: ISO-8601 timestamp of the block.
        token_symbol: ERC-20 symbol if applicable (empty for native).
        token_contract: ERC-20 contract address if applicable.
        gas_used: Gas consumed (EVM only, Decimal).
        gas_price_gwei: Gas price in gwei (EVM only, Decimal).
    """

    edge_id: str = Field(
        description="Deterministic ID: '{tx_hash}:{log_index_or_vout}'.",
    )
    from_node_id: str = Field(
        description="Source node ID (chain:address).",
    )
    to_node_id: str = Field(
        description="Destination node ID (chain:address).",
    )
    chain: ChainId = Field(
        description="Source blockchain identifier.",
    )
    edge_type: EdgeType = Field(
        description="Transfer classification.",
    )
    value: Decimal = Field(
        default=Decimal("0"),
        description="Amount in human-readable native units.",
    )
    value_usd: Decimal = Field(
        default=Decimal("0"),
        description="Estimated USD value at transfer time.",
    )
    tx_hash: str = Field(
        default="",
        description="On-chain transaction hash.",
    )
    block_number: int = Field(
        default=0,
        description="Block height.",
    )
    timestamp: str = Field(
        default="",
        description="ISO-8601 block timestamp.",
    )
    token_symbol: str = Field(
        default="",
        description="ERC-20 symbol (empty for native transfers).",
    )
    token_contract: str = Field(
        default="",
        description="ERC-20 contract address (empty for native).",
    )
    gas_used: Decimal = Field(
        default=Decimal("0"),
        description="Gas consumed (EVM only).",
    )
    gas_price_gwei: Decimal = Field(
        default=Decimal("0"),
        description="Gas price in gwei (EVM only).",
    )


# ─────────────────────── GoG Graph Response ──────────────────────────────────


class GoGGraphResponse(BaseModel, frozen=True):
    """Complete GoG subgraph returned by trace tools.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Contains all discovered nodes and edges for a single query,
    plus metadata about the query parameters and data quality.
    """

    nodes: list[GoGNode] = Field(
        default_factory=list,
        description="All discovered GoG nodes.",
    )
    edges: list[GoGEdge] = Field(
        default_factory=list,
        description="All discovered GoG edges.",
    )
    query_address: str = Field(
        default="",
        description="The address that was queried.",
    )
    query_chain: ChainId = Field(
        default=ChainId.ETHEREUM,
        description="The chain queried.",
    )
    depth_blocks: int = Field(
        default=0,
        description="Number of blocks scanned.",
    )
    status: str = Field(
        default="OK",
        description="OK | DEGRADED | ERROR.",
    )
    fetched_at: str = Field(
        default="",
        description="ISO-8601 timestamp of the query.",
    )


# ─────────────────────── Bridge Activity Report ──────────────────────────────


class BridgeInteraction(BaseModel, frozen=True):
    """A single interaction with a known cross-chain bridge.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Captures the bridge contract, direction, estimated destination
    chain, and capital volume for contagion analysis.
    """

    bridge_name: str = Field(
        description="Human-readable bridge name (e.g. 'Wormhole').",
    )
    bridge_contract: str = Field(
        description="Bridge contract address.",
    )
    direction: str = Field(
        description="'deposit' or 'withdrawal'.",
    )
    estimated_destination_chain: str = Field(
        default="unknown",
        description="Best-guess destination chain.",
    )
    value: Decimal = Field(
        default=Decimal("0"),
        description="Capital volume in native units.",
    )
    value_usd: Decimal = Field(
        default=Decimal("0"),
        description="Estimated USD value.",
    )
    tx_hash: str = Field(
        default="",
        description="Transaction hash.",
    )
    timestamp: str = Field(
        default="",
        description="ISO-8601 timestamp.",
    )


class BridgeActivityReport(BaseModel, frozen=True):
    """Aggregated cross-chain bridge activity for a single address.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    The ``OnChainIntelligenceAgent`` uses this to detect capital
    rotation patterns: e.g. an Ethereum whale liquidating stETH,
    bridging via Wormhole, and accumulating on Solana.
    """

    address: str = Field(
        description="Queried wallet address.",
    )
    chain: ChainId = Field(
        description="Source chain.",
    )
    interactions: list[BridgeInteraction] = Field(
        default_factory=list,
        description="All detected bridge interactions.",
    )
    total_bridge_volume: Decimal = Field(
        default=Decimal("0"),
        description="Aggregate bridged volume in native units.",
    )
    total_bridge_volume_usd: Decimal = Field(
        default=Decimal("0"),
        description="Aggregate bridged volume in USD.",
    )
    unique_bridges_used: int = Field(
        default=0,
        description="Count of distinct bridge contracts interacted with.",
    )
    estimated_destination_chains: list[str] = Field(
        default_factory=list,
        description="Deduplicated list of estimated destination chains.",
    )
    contagion_risk_level: str = Field(
        default="LOW",
        description="LOW | MEDIUM | HIGH — based on volume and diversity.",
    )
    status: str = Field(
        default="OK",
        description="OK | DEGRADED | ERROR.",
    )
    fetched_at: str = Field(
        default="",
        description="ISO-8601 timestamp.",
    )


# ─────────────────────── Serialization Helpers ───────────────────────────────


def encode_model(model: BaseModel) -> str:
    """Serialize a Pydantic model to JSON string via msgspec.

    Uses the custom Decimal-aware encoder to preserve full precision.
    """
    raw_dict: dict[str, Any] = model.model_dump(mode="json")
    return ENCODER.encode(raw_dict).decode("utf-8")
