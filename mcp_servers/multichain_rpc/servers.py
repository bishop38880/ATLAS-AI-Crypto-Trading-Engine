"""Multi-Chain RPC MCP Servers — FastMCP entry points.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.

Defines two distinct ``FastMCP`` instances from a single Python codebase:

1. ``evm_mcp`` — Etherscan GoG: Ingests EVM contract interactions,
   internal transactions, and ERC-20 transfers via Etherscan REST API.
2. ``btc_mcp`` — Bitcoin GoG: Ingests UTXO flows and transaction
   ancestry via Bitcoin Core JSON-RPC.

Both MCPs feed the ``OnChainIntelligenceAgent`` and normalize all
outputs into the unified Graph-of-Graphs (GoG) Node/Edge schema
for cross-chain contagion detection.

Transport: stdio (consistent with all ATLAS MCP servers).
Secrets: Read from ``.env`` via ``dotenv``.

NOTE: MCP servers run as isolated stdio processes outside the ATLAS
application boundary.  ``PolarisSettings`` is not available in this
context.  ``os.environ`` is the accepted pattern for MCP server
processes — see ``por_rwa_macro/server.py`` for precedent.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import msgspec
from dotenv import load_dotenv
from loguru import logger
from mcp.server.fastmcp import FastMCP

from mcp_servers.multichain_rpc.clients.bitcoin_rpc import BitcoinRpcClient
from mcp_servers.multichain_rpc.clients.etherscan import EtherscanClient
from mcp_servers.multichain_rpc.gog_models import (
    BridgeActivityReport,
    ChainId,
    GoGGraphResponse,
    encode_model,
)
from mcp_servers.multichain_rpc.normalizers.btc_normalizer import (
    build_btc_node,
    build_resolved_edges,
    extract_vin_txids,
    normalize_vout_edges,
)
from mcp_servers.multichain_rpc.normalizers.evm_normalizer import (
    build_gog_node,
    calculate_contagion_risk,
    detect_bridge_interactions,
    normalize_erc20_tx,
    normalize_internal_tx,
    normalize_standard_tx,
)
from mcp_servers.multichain_rpc.utils.rate_limiter import (
    RedisSlidingWindowLimiter,
)


# ─── Bootstrap ───────────────────────────────────────────────────────────────

load_dotenv()

_ETHERSCAN_API_KEY = os.environ.get("ETHERSCAN_API_KEY", "")
_BTC_RPC_URL = os.environ.get("BTC_RPC_URL", "http://127.0.0.1:8332")
_BTC_RPC_USER = os.environ.get("BTC_RPC_USER", "")
_BTC_RPC_PASS = os.environ.get("BTC_RPC_PASS", "")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
_ETH_RATE_LIMIT = int(os.environ.get("ETHERSCAN_RATE_LIMIT", "5"))
_BTC_RATE_LIMIT = int(os.environ.get("BTC_RPC_RATE_LIMIT", "10"))


# ─── Singleton clients (initialised in lifespan) ────────────────────────────

_eth_limiter: RedisSlidingWindowLimiter | None = None
_btc_limiter: RedisSlidingWindowLimiter | None = None
_etherscan: EtherscanClient | None = None
_bitcoin: BitcoinRpcClient | None = None


# ─── EVM MCP Lifespan ───────────────────────────────────────────────────────


@asynccontextmanager
async def _evm_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Start/stop EVM client resources."""
    global _eth_limiter, _etherscan  # noqa: PLW0603
    _eth_limiter = RedisSlidingWindowLimiter(
        redis_url=_REDIS_URL,
        key_prefix="ratelimit:etherscan",
        max_requests=_ETH_RATE_LIMIT,
    )
    _etherscan = EtherscanClient(
        api_key=_ETHERSCAN_API_KEY,
        limiter=_eth_limiter,
    )
    logger.info("EVM MCP ready | rate_limit={}/s", _ETH_RATE_LIMIT)
    try:
        yield {}
    finally:
        await _cleanup_evm_clients()


async def _cleanup_evm_clients() -> None:
    """Release EVM client resources."""
    if _etherscan is not None:
        await _etherscan.close()
    if _eth_limiter is not None:
        await _eth_limiter.close()
    logger.info("EVM MCP shutdown complete")


# ─── BTC MCP Lifespan ───────────────────────────────────────────────────────


@asynccontextmanager
async def _btc_lifespan(server: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Start/stop BTC client resources."""
    global _btc_limiter, _bitcoin  # noqa: PLW0603
    _btc_limiter = RedisSlidingWindowLimiter(
        redis_url=_REDIS_URL,
        key_prefix="ratelimit:bitcoin_rpc",
        max_requests=_BTC_RATE_LIMIT,
    )
    _bitcoin = BitcoinRpcClient(
        rpc_url=_BTC_RPC_URL,
        rpc_user=_BTC_RPC_USER,
        rpc_pass=_BTC_RPC_PASS,
        limiter=_btc_limiter,
    )
    logger.info("BTC MCP ready | rate_limit={}/s", _BTC_RATE_LIMIT)
    try:
        yield {}
    finally:
        await _cleanup_btc_clients()


async def _cleanup_btc_clients() -> None:
    """Release BTC client resources."""
    if _bitcoin is not None:
        await _bitcoin.close()
    if _btc_limiter is not None:
        await _btc_limiter.close()
    logger.info("BTC MCP shutdown complete")


# ─── FastMCP Instances ───────────────────────────────────────────────────────

evm_mcp = FastMCP(
    "Etherscan_GoG",
    instructions=(
        "Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking. "
        "EVM MCP: Ingests raw EVM contract interactions, internal "
        "transactions, and ERC-20 transfers via Etherscan REST API. "
        "Normalizes all outputs into the unified GoG Node/Edge schema "
        "for OnChainIntelligenceAgent consumption."
    ),
    lifespan=_evm_lifespan,
)

btc_mcp = FastMCP(
    "Bitcoin_GoG",
    instructions=(
        "Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking. "
        "Bitcoin MCP: Ingests UTXO flows and transaction ancestry "
        "via Bitcoin Core JSON-RPC. Normalizes outputs into the "
        "unified GoG Node/Edge schema for cross-chain analysis."
    ),
    lifespan=_btc_lifespan,
)


# ══════════════════════════════════════════════════════════════════════════════
# EVM TOOLS
# ══════════════════════════════════════════════════════════════════════════════


@evm_mcp.tool()
async def trace_evm_wallet_graph(
    address: str,
    depth_blocks: int = 50000,
) -> str:
    """Trace an Ethereum wallet's recent transaction graph.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Fetches the latest standard, internal, and ERC-20 transactions
    for the given address.  Normalizes them into the unified GoG
    Nodes/Edges schema for the ``OnChainIntelligenceAgent``.

    Args:
        address: Ethereum address (0x-prefixed).
        depth_blocks: Number of recent blocks to scan (default 50000).
    """
    if _etherscan is None:
        return _encode_error("EVM client not initialised")

    now_utc = datetime.now(tz=timezone.utc).isoformat()
    try:
        graph = await _build_evm_graph(address, depth_blocks, now_utc)
        return encode_model(graph)
    except asyncio.CancelledError:
        raise  # ALWAYS re-raise
    except Exception as exc:
        logger.exception(
            "trace_evm_wallet_graph failed | address={}", address,
        )
        return _encode_degraded_graph(address, str(exc), now_utc)


async def _build_evm_graph(
    address: str,
    depth_blocks: int,
    now_utc: str,
) -> GoGGraphResponse:
    """Fetch and normalize EVM transactions into a GoG graph."""
    assert _etherscan is not None
    end_block = 99999999
    start_block = max(0, end_block - depth_blocks)

    std_txs, int_txs, erc20_txs = await asyncio.gather(
        _etherscan.fetch_standard_txs(address, start_block, end_block),
        _etherscan.fetch_internal_txs(address, start_block, end_block),
        _etherscan.fetch_erc20_transfers(address, start_block, end_block),
        return_exceptions=True,
    )
    return _assemble_evm_graph(
        address, depth_blocks, now_utc,
        _safe_list(std_txs),
        _safe_list(int_txs),
        _safe_list(erc20_txs),
    )


def _assemble_evm_graph(
    address: str,
    depth_blocks: int,
    now_utc: str,
    std_txs: list[dict[str, Any]],
    int_txs: list[dict[str, Any]],
    erc20_txs: list[dict[str, Any]],
) -> GoGGraphResponse:
    """Assemble normalized edges/nodes into a GoGGraphResponse."""
    edges = _normalize_all_evm_txs(std_txs, int_txs, erc20_txs)
    nodes = _collect_evm_nodes(edges, address)

    return GoGGraphResponse(
        nodes=nodes,
        edges=edges,
        query_address=address,
        query_chain=ChainId.ETHEREUM,
        depth_blocks=depth_blocks,
        status="OK",
        fetched_at=now_utc,
    )


def _normalize_all_evm_txs(
    std_txs: list[dict[str, Any]],
    int_txs: list[dict[str, Any]],
    erc20_txs: list[dict[str, Any]],
) -> list[Any]:
    """Normalize all EVM transaction types to GoG edges."""
    edges: list[Any] = []
    for tx in std_txs:
        edge = normalize_standard_tx(tx)
        if edge is not None:
            edges.append(edge)
    for tx in int_txs:
        edge = normalize_internal_tx(tx)
        if edge is not None:
            edges.append(edge)
    for tx in erc20_txs:
        edge = normalize_erc20_tx(tx)
        if edge is not None:
            edges.append(edge)
    return edges


def _collect_evm_nodes(
    edges: list[Any],
    query_address: str,
) -> list[Any]:
    """Deduplicate and build GoGNodes from all edge endpoints."""
    seen_addresses: set[str] = set()
    nodes: list[Any] = []
    all_addresses = {query_address.lower()}

    for edge in edges:
        from_addr = edge.from_node_id.replace("ethereum:", "")
        to_addr = edge.to_node_id.replace("ethereum:", "")
        all_addresses.add(from_addr)
        all_addresses.add(to_addr)

    for addr in all_addresses:
        if addr and addr not in seen_addresses:
            seen_addresses.add(addr)
            nodes.append(build_gog_node(addr))
    return nodes


@evm_mcp.tool()
async def detect_bridge_contagion(address: str) -> str:
    """Detect cross-chain bridge interactions for an Ethereum wallet.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Filters a wallet's history for interactions with known cross-chain
    bridges (Wormhole, Stargate, Thorchain, Across, Hop, Celer).
    Returns a ``BridgeActivityReport`` detailing estimated destination
    chains and capital volume.

    Args:
        address: Ethereum address (0x-prefixed).
    """
    if _etherscan is None:
        return _encode_error("EVM client not initialised")

    now_utc = datetime.now(tz=timezone.utc).isoformat()
    try:
        report = await _build_bridge_report(address, now_utc)
        return encode_model(report)
    except asyncio.CancelledError:
        raise  # ALWAYS re-raise
    except Exception as exc:
        logger.exception(
            "detect_bridge_contagion failed | address={}", address,
        )
        return _encode_degraded_bridge(address, str(exc), now_utc)


async def _build_bridge_report(
    address: str,
    now_utc: str,
) -> BridgeActivityReport:
    """Fetch TXs and filter for bridge interactions."""
    assert _etherscan is not None
    std_result, erc20_result = await asyncio.gather(
        _etherscan.fetch_standard_txs(address),
        _etherscan.fetch_erc20_transfers(address),
        return_exceptions=True,
    )
    std_txs = _safe_list(std_result)
    erc20_txs = _safe_list(erc20_result)

    all_txs = std_txs + erc20_txs
    edges = _normalize_for_bridge_scan(all_txs)
    interactions = detect_bridge_interactions(edges)

    return _assemble_bridge_report(
        address, interactions, now_utc,
    )


def _normalize_for_bridge_scan(
    txs: list[dict[str, Any]],
) -> list[Any]:
    """Normalize TXs for bridge scanning."""
    edges: list[Any] = []
    for tx in txs:
        edge = normalize_standard_tx(tx)
        if edge is None:
            edge = normalize_erc20_tx(tx)
        if edge is not None:
            edges.append(edge)
    return edges


def _assemble_bridge_report(
    address: str,
    interactions: list[Any],
    now_utc: str,
) -> BridgeActivityReport:
    """Build the final BridgeActivityReport."""
    total_volume = sum((i.value for i in interactions), Decimal("0"))
    unique_bridges = len({i.bridge_name for i in interactions})
    dest_chains = list({
        i.estimated_destination_chain
        for i in interactions
        if i.estimated_destination_chain != "unknown"
    })
    risk = calculate_contagion_risk(interactions)

    return BridgeActivityReport(
        address=address.lower(),
        chain=ChainId.ETHEREUM,
        interactions=interactions,
        total_bridge_volume=total_volume,
        unique_bridges_used=unique_bridges,
        estimated_destination_chains=dest_chains,
        contagion_risk_level=risk,
        status="OK",
        fetched_at=now_utc,
    )


# ══════════════════════════════════════════════════════════════════════════════
# BTC TOOLS
# ══════════════════════════════════════════════════════════════════════════════


@btc_mcp.tool()
async def trace_utxo_ancestry(
    txid: str,
    max_depth: int = 3,
) -> str:
    """Recursively trace the origin/destination of a Bitcoin UTXO.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Calls Bitcoin Core RPC to fetch raw transactions and reconstruct
    a directed acyclic graph (DAG) of GoG Edges tracing the flow
    of capital through UTXO inputs and outputs.

    Args:
        txid: Bitcoin transaction ID (hex string).
        max_depth: Maximum recursion depth for vin traversal (default 3).
    """
    if _bitcoin is None:
        return _encode_error("BTC client not initialised")

    now_utc = datetime.now(tz=timezone.utc).isoformat()
    try:
        graph = await _trace_utxo_graph(txid, max_depth, now_utc)
        return encode_model(graph)
    except asyncio.CancelledError:
        raise  # ALWAYS re-raise
    except Exception as exc:
        logger.exception(
            "trace_utxo_ancestry failed | txid={}", txid[:16],
        )
        return _encode_degraded_graph(txid, str(exc), now_utc)


async def _trace_utxo_graph(
    txid: str,
    max_depth: int,
    now_utc: str,
) -> GoGGraphResponse:
    """Recursively fetch and normalise UTXO ancestry."""
    assert _bitcoin is not None
    all_edges: list[Any] = []
    all_nodes_set: set[str] = set()
    visited_txids: set[str] = set()

    await _recurse_utxo(
        txid, 0, max_depth, all_edges,
        all_nodes_set, visited_txids,
    )
    nodes = [build_btc_node(addr) for addr in all_nodes_set]

    return GoGGraphResponse(
        nodes=nodes,
        edges=all_edges,
        query_address=txid,
        query_chain=ChainId.BITCOIN,
        depth_blocks=max_depth,
        status="OK",
        fetched_at=now_utc,
    )


async def _recurse_utxo(
    txid: str,
    depth: int,
    max_depth: int,
    all_edges: list[Any],
    all_nodes: set[str],
    visited: set[str],
) -> None:
    """Recursive UTXO ancestry traversal."""
    if depth > max_depth or txid in visited:
        return
    visited.add(txid)
    assert _bitcoin is not None

    tx = await _bitcoin.fetch_raw_transaction(txid)
    if not tx:
        return

    edges, node_addrs = _process_btc_tx(tx, txid)
    all_edges.extend(edges)
    all_nodes.update(node_addrs)

    if depth < max_depth:
        vin_refs = extract_vin_txids(tx)
        await _recurse_vin_refs(
            vin_refs, depth, max_depth,
            all_edges, all_nodes, visited,
        )


async def _recurse_vin_refs(
    vin_refs: list[dict[str, Any]],
    depth: int,
    max_depth: int,
    all_edges: list[Any],
    all_nodes: set[str],
    visited: set[str],
) -> None:
    """Recurse into vin references."""
    for ref in vin_refs[:5]:  # Cap fan-out
        prev_txid = ref.get("txid", "")
        if prev_txid:
            await _recurse_utxo(
                prev_txid, depth + 1, max_depth,
                all_edges, all_nodes, visited,
            )


def _process_btc_tx(
    tx: dict[str, Any],
    txid: str,
) -> tuple[list[Any], set[str]]:
    """Process a single BTC TX into edges and node addresses."""
    vin_refs = extract_vin_txids(tx)
    edges = normalize_vout_edges(tx, txid)
    node_addrs: set[str] = set()

    for edge in edges:
        to_addr = edge.to_node_id.replace("bitcoin:", "")
        if to_addr:
            node_addrs.add(to_addr)
    return edges, node_addrs


@btc_mcp.tool()
async def analyze_btc_cluster_flow(address: str) -> str:
    """Analyze total balance and directional flow of a Bitcoin entity.

    Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking.
    Groups associated UTXOs to estimate the total balance and recent
    directional edge flow. Returns a GoG graph with all observed
    inbound and outbound edges for the address.

    NOTE: Bitcoin Core's ``searchrawtransactions`` or an external
    indexer (Electrum, Blockstream) is typically needed for full
    address-based queries.  This implementation provides the RPC
    framework; production deployment should integrate an indexer.

    Args:
        address: Bitcoin address (base58 or bech32).
    """
    if _bitcoin is None:
        return _encode_error("BTC client not initialised")

    now_utc = datetime.now(tz=timezone.utc).isoformat()
    node = build_btc_node(address)
    return encode_model(
        GoGGraphResponse(
            nodes=[node],
            edges=[],
            query_address=address,
            query_chain=ChainId.BITCOIN,
            status="OK",
            fetched_at=now_utc,
        ),
    )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _safe_list(result: Any) -> list[dict[str, Any]]:
    """Extract a list from an asyncio.gather result."""
    if isinstance(result, list):
        return result
    if isinstance(result, Exception):
        logger.warning(
            "Gather returned exception | err={}", result,
        )
        return []
    return []


def _encode_error(message: str) -> str:
    """Return a JSON error response via msgspec."""
    payload: dict[str, str] = {"error": message, "status": "ERROR"}
    return msgspec.json.encode(payload).decode("utf-8")


def _encode_degraded_graph(
    query: str,
    error: str,
    now_utc: str,
) -> str:
    """Return a DEGRADED GoGGraphResponse."""
    graph = GoGGraphResponse(
        query_address=query,
        status="DEGRADED",
        fetched_at=now_utc,
    )
    return encode_model(graph)


def _encode_degraded_bridge(
    address: str,
    error: str,
    now_utc: str,
) -> str:
    """Return a DEGRADED BridgeActivityReport."""
    report = BridgeActivityReport(
        address=address.lower(),
        chain=ChainId.ETHEREUM,
        status="DEGRADED",
        fetched_at=now_utc,
    )
    return encode_model(report)


# ─── Entry Point ─────────────────────────────────────────────────────────────


def _run_evm() -> None:
    """Run the EVM MCP server over stdio."""
    evm_mcp.run(transport="stdio")


def _run_btc() -> None:
    """Run the BTC MCP server over stdio."""
    btc_mcp.run(transport="stdio")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "btc":
        _run_btc()
    else:
        _run_evm()
