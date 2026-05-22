"""EVM transaction normalizer — Etherscan data → GoG schema.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Translates raw Etherscan ``txlist``, ``txlistinternal``, and ``tokentx``
responses into unified ``GoGNode`` and ``GoGEdge`` objects.  Handles
the Account-state model natively: every ``from``/``to`` address becomes
a node, every transaction becomes a directed edge.

Known bridge contracts are hardcoded for contagion detection (Wormhole,
Stargate, Thorchain, Across, Hop, Celer, Multichain).

All value conversions use ``Decimal`` — ``float`` is banned.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from mcp_servers.multichain_rpc.gog_models import (
    BridgeActivityReport,
    BridgeInteraction,
    ChainId,
    EdgeType,
    GoGEdge,
    GoGNode,
    NodeType,
)

# ─── Bridge Contract Registry ───────────────────────────────────────────────

KNOWN_BRIDGES: dict[str, dict[str, str]] = {
    # Wormhole Token Bridge (Ethereum)
    "0x3ee18b2214aff97000d974cf647e7c347e8fa585": {
        "name": "Wormhole",
        "destination_hint": "solana",
    },
    # Stargate Router (Ethereum)
    "0x8731d54e9d02c286767d56ac03e8037c07e01e98": {
        "name": "Stargate",
        "destination_hint": "multi-chain",
    },
    # Thorchain Router (Ethereum)
    "0xd37bbe5744d730a1d98d8dc97c42f0ca46ad7146": {
        "name": "Thorchain",
        "destination_hint": "bitcoin",
    },
    # Across Protocol SpokePool (Ethereum)
    "0x5c7bcd6e7de5005b46560433d7c7aab4d18c124c": {
        "name": "Across",
        "destination_hint": "multi-chain",
    },
    # Hop Protocol Bridge (Ethereum)
    "0xb8901acb165ed027e32754e0ffe830802919727f": {
        "name": "Hop",
        "destination_hint": "multi-chain",
    },
    # Celer cBridge (Ethereum)
    "0x5427fefa711eff984124bfbb1ab6fbf5e3da1820": {
        "name": "Celer",
        "destination_hint": "multi-chain",
    },
}

# Wei-to-ETH divisor
_WEI_DIVISOR = Decimal("1000000000000000000")
_GWEI_DIVISOR = Decimal("1000000000")


# ─── Node construction ──────────────────────────────────────────────────────


def build_node_id(address: str) -> str:
    """Build a deterministic GoG node ID for Ethereum.

    Args:
        address: Ethereum address (0x-prefixed).

    Returns:
        Node ID string: 'ethereum:{address_lower}'.
    """
    return f"ethereum:{address.lower()}"


def classify_node(address: str) -> NodeType:
    """Classify an Ethereum address as bridge, contract, or wallet.

    Args:
        address: Ethereum address.

    Returns:
        ``NodeType`` classification.
    """
    addr_lower = address.lower()
    if addr_lower in KNOWN_BRIDGES:
        return NodeType.BRIDGE_CONTRACT
    return NodeType.WALLET


def build_gog_node(address: str) -> GoGNode:
    """Construct a GoGNode from an Ethereum address.

    Args:
        address: Ethereum address.

    Returns:
        A frozen GoGNode instance.
    """
    addr_lower = address.lower()
    node_type = classify_node(addr_lower)
    label = ""
    if node_type == NodeType.BRIDGE_CONTRACT:
        bridge_info = KNOWN_BRIDGES.get(addr_lower, {})
        label = bridge_info.get("name", "")
    return GoGNode(
        node_id=build_node_id(addr_lower),
        address=addr_lower,
        chain=ChainId.ETHEREUM,
        node_type=node_type,
        label=label,
    )


# ─── Edge construction from standard TXs ────────────────────────────────────


def normalize_standard_tx(tx: dict[str, Any]) -> GoGEdge | None:
    """Convert a single Etherscan ``txlist`` entry to a GoGEdge.

    Args:
        tx: Raw transaction dict from Etherscan.

    Returns:
        A ``GoGEdge`` or ``None`` if the TX is malformed.
    """
    from_addr = tx.get("from", "")
    to_addr = tx.get("to", "")
    if not from_addr or not to_addr:
        return None

    value_wei = Decimal(str(tx.get("value", "0")))
    value_eth = value_wei / _WEI_DIVISOR

    gas_used = Decimal(str(tx.get("gasUsed", "0")))
    gas_price = Decimal(str(tx.get("gasPrice", "0"))) / _GWEI_DIVISOR

    tx_hash = tx.get("hash", "")
    block_number = int(tx.get("blockNumber", "0"))
    timestamp = _unix_to_iso(tx.get("timeStamp", "0"))

    return GoGEdge(
        edge_id=f"{tx_hash}:0",
        from_node_id=build_node_id(from_addr),
        to_node_id=build_node_id(to_addr),
        chain=ChainId.ETHEREUM,
        edge_type=EdgeType.TRANSFER,
        value=value_eth,
        tx_hash=tx_hash,
        block_number=block_number,
        timestamp=timestamp,
        gas_used=gas_used,
        gas_price_gwei=gas_price,
    )


# ─── Edge construction from internal TXs ────────────────────────────────────


def normalize_internal_tx(tx: dict[str, Any]) -> GoGEdge | None:
    """Convert a single Etherscan ``txlistinternal`` entry to a GoGEdge.

    Critical for detecting proxy liquidations and hidden value flows.

    Args:
        tx: Raw internal transaction dict.

    Returns:
        A ``GoGEdge`` or ``None``.
    """
    from_addr = tx.get("from", "")
    to_addr = tx.get("to", "")
    if not from_addr or not to_addr:
        return None

    value_eth = Decimal(str(tx.get("value", "0"))) / _WEI_DIVISOR
    tx_hash = tx.get("hash", "")
    block_number = int(tx.get("blockNumber", "0"))
    timestamp = _unix_to_iso(tx.get("timeStamp", "0"))

    return GoGEdge(
        edge_id=f"{tx_hash}:internal",
        from_node_id=build_node_id(from_addr),
        to_node_id=build_node_id(to_addr),
        chain=ChainId.ETHEREUM,
        edge_type=EdgeType.INTERNAL_TRANSFER,
        value=value_eth,
        tx_hash=tx_hash,
        block_number=block_number,
        timestamp=timestamp,
    )


# ─── Edge construction from ERC-20 transfers ────────────────────────────────


def normalize_erc20_tx(tx: dict[str, Any]) -> GoGEdge | None:
    """Convert a single Etherscan ``tokentx`` entry to a GoGEdge.

    Args:
        tx: Raw ERC-20 transfer dict.

    Returns:
        A ``GoGEdge`` or ``None``.
    """
    from_addr = tx.get("from", "")
    to_addr = tx.get("to", "")
    if not from_addr or not to_addr:
        return None

    token_decimals = int(tx.get("tokenDecimal", "18"))
    divisor = Decimal(10) ** token_decimals
    value = Decimal(str(tx.get("value", "0"))) / divisor

    tx_hash = tx.get("hash", "")
    block_number = int(tx.get("blockNumber", "0"))
    timestamp = _unix_to_iso(tx.get("timeStamp", "0"))
    token_symbol = tx.get("tokenSymbol", "")
    token_contract = tx.get("contractAddress", "")

    return GoGEdge(
        edge_id=f"{tx_hash}:erc20:{token_contract[:8]}",
        from_node_id=build_node_id(from_addr),
        to_node_id=build_node_id(to_addr),
        chain=ChainId.ETHEREUM,
        edge_type=EdgeType.ERC20_TRANSFER,
        value=value,
        tx_hash=tx_hash,
        block_number=block_number,
        timestamp=timestamp,
        token_symbol=token_symbol,
        token_contract=token_contract.lower(),
    )


# ─── Bridge detection ───────────────────────────────────────────────────────


def detect_bridge_interactions(
    edges: list[GoGEdge],
) -> list[BridgeInteraction]:
    """Filter edges for interactions with known bridge contracts.

    Args:
        edges: All GoGEdges for a wallet.

    Returns:
        List of ``BridgeInteraction`` objects.
    """
    interactions: list[BridgeInteraction] = []
    for edge in edges:
        interaction = _check_edge_for_bridge(edge)
        if interaction is not None:
            interactions.append(interaction)
    return interactions


def _check_edge_for_bridge(edge: GoGEdge) -> BridgeInteraction | None:
    """Check if a single edge involves a known bridge."""
    to_addr = edge.to_node_id.replace("ethereum:", "")
    from_addr = edge.from_node_id.replace("ethereum:", "")

    if to_addr in KNOWN_BRIDGES:
        bridge_info = KNOWN_BRIDGES[to_addr]
        return _build_bridge_interaction(
            bridge_info, to_addr, "deposit", edge,
        )
    if from_addr in KNOWN_BRIDGES:
        bridge_info = KNOWN_BRIDGES[from_addr]
        return _build_bridge_interaction(
            bridge_info, from_addr, "withdrawal", edge,
        )
    return None


def _build_bridge_interaction(
    bridge_info: dict[str, str],
    contract_addr: str,
    direction: str,
    edge: GoGEdge,
) -> BridgeInteraction:
    """Construct a BridgeInteraction from edge data."""
    return BridgeInteraction(
        bridge_name=bridge_info.get("name", "Unknown"),
        bridge_contract=contract_addr,
        direction=direction,
        estimated_destination_chain=bridge_info.get(
            "destination_hint", "unknown",
        ),
        value=edge.value,
        tx_hash=edge.tx_hash,
        timestamp=edge.timestamp,
    )


def calculate_contagion_risk(
    interactions: list[BridgeInteraction],
) -> str:
    """Determine contagion risk level from bridge interactions.

    Args:
        interactions: Detected bridge interactions.

    Returns:
        Risk level string: 'LOW', 'MEDIUM', or 'HIGH'.
    """
    if not interactions:
        return "LOW"
    unique_bridges = len({i.bridge_name for i in interactions})
    total_volume = sum(i.value for i in interactions)
    if unique_bridges >= 3 or total_volume > Decimal("100"):
        return "HIGH"
    if unique_bridges >= 2 or total_volume > Decimal("10"):
        return "MEDIUM"
    return "LOW"


# ─── Timestamp helper ───────────────────────────────────────────────────────


def _unix_to_iso(unix_str: Any) -> str:
    """Convert a Unix timestamp string to ISO-8601."""
    try:
        ts = int(str(unix_str))
        return datetime.fromtimestamp(
            ts, tz=timezone.utc,
        ).isoformat()
    except (ValueError, TypeError, OSError):
        return ""
