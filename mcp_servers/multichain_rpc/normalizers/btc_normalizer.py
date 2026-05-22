"""Bitcoin UTXO normalizer — Bitcoin Core RPC data → GoG schema.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Translates raw ``getrawtransaction`` (verbose) responses into unified
``GoGNode`` and ``GoGEdge`` objects.  Handles the UTXO model natively:
``vin`` inputs become inbound edges from previous outputs, ``vout``
outputs become outbound edges to new addresses.

All Satoshi → BTC conversions use ``Decimal("100000000")`` divisor.
``float`` is strictly banned for any value representing money.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from loguru import logger

from mcp_servers.multichain_rpc.gog_models import (
    ChainId,
    EdgeType,
    GoGEdge,
    GoGNode,
    NodeType,
)

# ─── Constants ───────────────────────────────────────────────────────────────

_SATOSHI_DIVISOR = Decimal("100000000")

# Known Bitcoin addresses (CEX hot wallets, etc.)
KNOWN_BTC_ENTITIES: dict[str, dict[str, str]] = {
    "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h": {
        "label": "Binance Cold Wallet",
        "type": "cex_hot_wallet",
    },
    "3FHNBLobJnbCTFTVakh5TXmEneyf5PT61B": {
        "label": "Binance Hot Wallet 1",
        "type": "cex_hot_wallet",
    },
    "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s": {
        "label": "Binance Hot Wallet 2",
        "type": "cex_hot_wallet",
    },
    "bc1qa5wkgaew2dkv56kc6hp23ly8kunz36qx85pmq0": {
        "label": "Kraken Hot Wallet",
        "type": "cex_hot_wallet",
    },
}


# ─── Node construction ──────────────────────────────────────────────────────


def build_btc_node_id(address: str) -> str:
    """Build a deterministic GoG node ID for Bitcoin.

    Args:
        address: Bitcoin address (base58 or bech32).

    Returns:
        Node ID string: 'bitcoin:{address}'.
    """
    return f"bitcoin:{address}"


def classify_btc_node(address: str) -> NodeType:
    """Classify a Bitcoin address using the known entities registry.

    Args:
        address: Bitcoin address.

    Returns:
        ``NodeType`` classification.
    """
    entity = KNOWN_BTC_ENTITIES.get(address)
    if entity is not None:
        type_str = entity.get("type", "wallet")
        if type_str == "cex_hot_wallet":
            return NodeType.CEX_HOT_WALLET
    return NodeType.WALLET


def build_btc_node(address: str) -> GoGNode:
    """Construct a GoGNode from a Bitcoin address.

    Args:
        address: Bitcoin address.

    Returns:
        A frozen GoGNode instance.
    """
    node_type = classify_btc_node(address)
    label = ""
    entity = KNOWN_BTC_ENTITIES.get(address)
    if entity is not None:
        label = entity.get("label", "")
    return GoGNode(
        node_id=build_btc_node_id(address),
        address=address,
        chain=ChainId.BITCOIN,
        node_type=node_type,
        label=label,
    )


# ─── UTXO → GoG Edge translation ────────────────────────────────────────────


def normalize_vout_edges(
    tx: dict[str, Any],
    txid: str,
) -> list[GoGEdge]:
    """Convert ``vout`` array to outbound GoG edges.

    Each ``vout`` entry with a valid address becomes a directed edge
    from an inferred source to the output address.

    Args:
        tx: Decoded raw transaction dict (verbose=true).
        txid: Transaction ID.

    Returns:
        List of ``GoGEdge`` objects for each vout.
    """
    edges: list[GoGEdge] = []
    vout_list = tx.get("vout", [])
    timestamp = _extract_timestamp(tx)

    for vout in vout_list:
        edge = _parse_single_vout(vout, txid, timestamp)
        if edge is not None:
            edges.append(edge)
    return edges


def _parse_single_vout(
    vout: dict[str, Any],
    txid: str,
    timestamp: str,
) -> GoGEdge | None:
    """Parse a single vout entry into a GoGEdge."""
    addresses = _extract_vout_addresses(vout)
    if not addresses:
        return None

    vout_index = int(vout.get("n", 0))
    value_btc = _to_decimal(vout.get("value", "0"))
    to_address = addresses[0]

    return GoGEdge(
        edge_id=f"{txid}:{vout_index}",
        from_node_id=f"bitcoin:coinbase_or_vin",
        to_node_id=build_btc_node_id(to_address),
        chain=ChainId.BITCOIN,
        edge_type=EdgeType.UTXO_SPEND,
        value=value_btc,
        tx_hash=txid,
        timestamp=timestamp,
    )


def _extract_vout_addresses(
    vout: dict[str, Any],
) -> list[str]:
    """Extract addresses from a vout scriptPubKey."""
    script_pub_key = vout.get("scriptPubKey", {})
    address = script_pub_key.get("address", "")
    if address:
        return [address]
    addresses = script_pub_key.get("addresses", [])
    if isinstance(addresses, list):
        return addresses
    return []


# ─── VIN processing ─────────────────────────────────────────────────────────


def extract_vin_txids(tx: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract previous transaction references from ``vin``.

    Args:
        tx: Decoded raw transaction dict.

    Returns:
        List of dicts with 'txid' and 'vout' index for each input.
    """
    vin_list = tx.get("vin", [])
    references: list[dict[str, Any]] = []
    for vin in vin_list:
        if _is_coinbase_vin(vin):
            continue
        ref = _parse_single_vin(vin)
        if ref is not None:
            references.append(ref)
    return references


def _is_coinbase_vin(vin: dict[str, Any]) -> bool:
    """Check if a vin is a coinbase input (mining reward)."""
    return "coinbase" in vin


def _parse_single_vin(
    vin: dict[str, Any],
) -> dict[str, Any] | None:
    """Parse a single vin entry into a reference dict."""
    prev_txid = vin.get("txid", "")
    prev_vout = vin.get("vout", 0)
    if not prev_txid:
        return None
    return {"txid": prev_txid, "vout": prev_vout}


# ─── Ancestry graph assembly ────────────────────────────────────────────────


def build_resolved_edges(
    tx: dict[str, Any],
    vin_sources: dict[str, dict[str, Any]],
    txid: str,
) -> list[GoGEdge]:
    """Build fully resolved edges by linking vin sources to vouts.

    For each vin, looks up the previous transaction's vout to find
    the source address, then creates an edge from source → each
    destination vout address.

    Args:
        tx: Current transaction dict.
        vin_sources: Map of prev_txid → prev transaction dict.
        txid: Current transaction ID.

    Returns:
        List of resolved GoGEdges.
    """
    edges: list[GoGEdge] = []
    timestamp = _extract_timestamp(tx)
    source_addresses = _resolve_vin_sources(tx, vin_sources)

    vout_list = tx.get("vout", [])
    for vout in vout_list:
        new_edges = _build_edges_for_vout(
            vout, txid, timestamp, source_addresses,
        )
        edges.extend(new_edges)
    return edges


def _resolve_vin_sources(
    tx: dict[str, Any],
    vin_sources: dict[str, dict[str, Any]],
) -> list[str]:
    """Resolve vin entries to their source addresses."""
    source_addresses: list[str] = []
    for vin in tx.get("vin", []):
        if _is_coinbase_vin(vin):
            continue
        addr = _resolve_single_vin_address(vin, vin_sources)
        if addr:
            source_addresses.append(addr)
    return source_addresses


def _resolve_single_vin_address(
    vin: dict[str, Any],
    vin_sources: dict[str, dict[str, Any]],
) -> str:
    """Resolve a single vin to its source address."""
    prev_txid = vin.get("txid", "")
    prev_vout_idx = int(vin.get("vout", 0))
    prev_tx = vin_sources.get(prev_txid, {})
    prev_vouts = prev_tx.get("vout", [])

    if prev_vout_idx < len(prev_vouts):
        prev_vout = prev_vouts[prev_vout_idx]
        addrs = _extract_vout_addresses(prev_vout)
        if addrs:
            return addrs[0]
    return ""


def _build_edges_for_vout(
    vout: dict[str, Any],
    txid: str,
    timestamp: str,
    source_addresses: list[str],
) -> list[GoGEdge]:
    """Build edges from all source addresses to a single vout."""
    dest_addrs = _extract_vout_addresses(vout)
    if not dest_addrs:
        return []

    vout_index = int(vout.get("n", 0))
    value_btc = _to_decimal(vout.get("value", "0"))
    to_address = dest_addrs[0]
    edges: list[GoGEdge] = []

    for src_addr in source_addresses:
        if not src_addr:
            continue
        edge = GoGEdge(
            edge_id=f"{txid}:{vout_index}:from:{src_addr[:8]}",
            from_node_id=build_btc_node_id(src_addr),
            to_node_id=build_btc_node_id(to_address),
            chain=ChainId.BITCOIN,
            edge_type=EdgeType.UTXO_SPEND,
            value=value_btc,
            tx_hash=txid,
            timestamp=timestamp,
        )
        edges.append(edge)
    return edges


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    """Safely cast any value to ``Decimal``."""
    if value is None:
        return Decimal(default)
    try:
        result = Decimal(str(value))
        if result.is_nan() or result.is_infinite():
            return Decimal(default)
        return result
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _extract_timestamp(tx: dict[str, Any]) -> str:
    """Extract and convert block time to ISO-8601."""
    block_time = tx.get("blocktime", tx.get("time", 0))
    if not block_time:
        return ""
    try:
        return datetime.fromtimestamp(
            int(block_time), tz=timezone.utc,
        ).isoformat()
    except (ValueError, TypeError, OSError):
        return ""
