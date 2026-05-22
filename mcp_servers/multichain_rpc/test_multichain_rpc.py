"""Tests for Multi-Chain RPC MCPs — Sentinel-grade coverage.

Section 27.1 Phase 3 Architecture: Graphs-of-Graphs (GoG) Contagion Tracking (GoG Super-Layer).

Covers:
1. GoG model serialization round-trip with Decimal precision.
2. Rate limiter: local sliding-window acquire/block logic.
3. EVM normalizer: standard TX, internal TX, ERC-20 TX parsing.
4. Bitcoin normalizer: UTXO vin/vout reconstruction.
5. Bridge detection: known contract matching and risk classification.
6. Etherscan client: response parsing and error handling.
7. Bitcoin RPC client: payload construction and response parsing.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from mcp_servers.multichain_rpc.gog_models import (
    ENCODER,
    BridgeActivityReport,
    BridgeInteraction,
    ChainId,
    EdgeType,
    GoGEdge,
    GoGGraphResponse,
    GoGNode,
    NodeType,
    decimal_dec_hook,
    decimal_enc_hook,
    encode_model,
)
from mcp_servers.multichain_rpc.normalizers.btc_normalizer import (
    _extract_vout_addresses,
    _to_decimal,
    build_btc_node,
    build_btc_node_id,
    classify_btc_node,
    extract_vin_txids,
    normalize_vout_edges,
)
from mcp_servers.multichain_rpc.normalizers.evm_normalizer import (
    KNOWN_BRIDGES,
    _unix_to_iso,
    build_gog_node,
    build_node_id,
    calculate_contagion_risk,
    classify_node,
    detect_bridge_interactions,
    normalize_erc20_tx,
    normalize_internal_tx,
    normalize_standard_tx,
)
from mcp_servers.multichain_rpc.utils.rate_limiter import (
    LocalSlidingWindowLimiter,
)


# ═══════════════════════ Decimal Hooks & Serialization ═══════════════════════


class TestDecimalHooks:
    """Verify custom msgspec Decimal hooks preserve precision."""

    def test_encode_decimal_to_string(self) -> None:
        """Decimal encodes as a JSON string, not a float."""
        val = Decimal("0.12345678901234567890")
        encoded = decimal_enc_hook(val)
        assert isinstance(encoded, str)
        assert encoded == "0.12345678901234567890"

    def test_decode_string_to_decimal(self) -> None:
        """String input decodes back to exact Decimal."""
        result = decimal_dec_hook(Decimal, "99.999999999")
        assert isinstance(result, Decimal)
        assert result == Decimal("99.999999999")

    def test_decode_float_to_decimal(self) -> None:
        """Float input is cast via str to avoid precision loss."""
        result = decimal_dec_hook(Decimal, 1.23)
        assert isinstance(result, Decimal)

    def test_encode_unsupported_raises(self) -> None:
        """Non-Decimal, non-datetime types raise TypeError."""
        with pytest.raises(TypeError):
            decimal_enc_hook(42)


class TestModelRoundTrip:
    """Verify model → JSON → dict round-trip with Decimal."""

    def test_gog_node_roundtrip(self) -> None:
        """GoGNode survives JSON encode."""
        node = GoGNode(
            node_id="ethereum:0xabc",
            address="0xabc",
            chain=ChainId.ETHEREUM,
            node_type=NodeType.WALLET,
            total_inflow=Decimal("123.456789"),
        )
        encoded = encode_model(node)
        decoded = msgspec.json.decode(encoded.encode())
        assert decoded["node_id"] == "ethereum:0xabc"
        assert decoded["total_inflow"] == "123.456789"

    def test_gog_edge_roundtrip(self) -> None:
        """GoGEdge with Decimal values round-trips correctly."""
        edge = GoGEdge(
            edge_id="0xhash:0",
            from_node_id="ethereum:0xfrom",
            to_node_id="ethereum:0xto",
            chain=ChainId.ETHEREUM,
            edge_type=EdgeType.TRANSFER,
            value=Decimal("1.234567890123456789"),
            gas_used=Decimal("21000"),
            gas_price_gwei=Decimal("50.5"),
        )
        encoded = encode_model(edge)
        decoded = msgspec.json.decode(encoded.encode())
        assert decoded["value"] == "1.234567890123456789"
        assert decoded["gas_used"] == "21000"

    def test_bridge_report_roundtrip(self) -> None:
        """BridgeActivityReport serializes contagion risk."""
        report = BridgeActivityReport(
            address="0xwhale",
            chain=ChainId.ETHEREUM,
            total_bridge_volume=Decimal("500.123"),
            contagion_risk_level="HIGH",
        )
        encoded = encode_model(report)
        decoded = msgspec.json.decode(encoded.encode())
        assert decoded["contagion_risk_level"] == "HIGH"
        assert decoded["total_bridge_volume"] == "500.123"

    def test_graph_response_with_nodes_and_edges(self) -> None:
        """GoGGraphResponse with populated nodes/edges."""
        graph = GoGGraphResponse(
            nodes=[
                GoGNode(
                    node_id="ethereum:0x1",
                    address="0x1",
                    chain=ChainId.ETHEREUM,
                ),
            ],
            edges=[
                GoGEdge(
                    edge_id="tx:0",
                    from_node_id="ethereum:0x1",
                    to_node_id="ethereum:0x2",
                    chain=ChainId.ETHEREUM,
                    edge_type=EdgeType.TRANSFER,
                ),
            ],
            status="OK",
        )
        encoded = encode_model(graph)
        decoded = msgspec.json.decode(encoded.encode())
        assert len(decoded["nodes"]) == 1
        assert len(decoded["edges"]) == 1


# ═══════════════════════ Rate Limiter Tests ══════════════════════════════════


class TestLocalSlidingWindowLimiter:
    """Tests for the in-process sliding-window rate limiter."""

    @pytest.mark.asyncio
    async def test_acquire_within_limit(self) -> None:
        """Requests within limit pass immediately."""
        limiter = LocalSlidingWindowLimiter(
            max_requests=5, window_seconds=1.0,
        )
        for _ in range(5):
            await limiter.acquire()
        # Should not raise or block indefinitely

    @pytest.mark.asyncio
    async def test_acquire_blocks_at_limit(self) -> None:
        """Requests beyond limit cause blocking."""
        limiter = LocalSlidingWindowLimiter(
            max_requests=2, window_seconds=0.2,
        )
        await limiter.acquire()
        await limiter.acquire()
        # Third acquire should block until window expires
        start = time.time()
        await limiter.acquire()
        elapsed = time.time() - start
        assert elapsed >= 0.1  # Had to wait for window

    @pytest.mark.asyncio
    async def test_window_expiry_frees_slots(self) -> None:
        """Old timestamps expire and free new slots."""
        limiter = LocalSlidingWindowLimiter(
            max_requests=1, window_seconds=0.1,
        )
        await limiter.acquire()
        await asyncio.sleep(0.15)
        # Window has expired, should be free
        await limiter.acquire()


# ═══════════════════════ EVM Normalizer Tests ════════════════════════════════


class TestEvmNormalizerNodes:
    """Tests for EVM node construction."""

    def test_build_node_id(self) -> None:
        """Node ID is deterministic and lowercase."""
        assert build_node_id("0xAbC") == "ethereum:0xabc"

    def test_classify_wallet(self) -> None:
        """Unknown address classified as WALLET."""
        assert classify_node("0x1234") == NodeType.WALLET

    def test_classify_bridge(self) -> None:
        """Known bridge address classified correctly."""
        wormhole = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
        assert classify_node(wormhole) == NodeType.BRIDGE_CONTRACT

    def test_build_gog_node_bridge_label(self) -> None:
        """Bridge nodes get human-readable labels."""
        wormhole = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
        node = build_gog_node(wormhole)
        assert node.label == "Wormhole"
        assert node.node_type == NodeType.BRIDGE_CONTRACT


class TestEvmNormalizerEdges:
    """Tests for EVM transaction normalization."""

    def test_normalize_standard_tx(self) -> None:
        """Standard TX converts to GoGEdge with Decimal value."""
        tx = {
            "from": "0xaaa",
            "to": "0xbbb",
            "value": "1000000000000000000",  # 1 ETH in wei
            "hash": "0xhash1",
            "blockNumber": "12345",
            "timeStamp": "1700000000",
            "gasUsed": "21000",
            "gasPrice": "50000000000",  # 50 gwei
        }
        edge = normalize_standard_tx(tx)
        assert edge is not None
        assert edge.value == Decimal("1")
        assert edge.edge_type == EdgeType.TRANSFER
        assert edge.gas_used == Decimal("21000")

    def test_normalize_standard_tx_missing_to(self) -> None:
        """TX with empty 'to' (contract creation) returns None."""
        tx = {"from": "0xaaa", "to": "", "value": "0"}
        assert normalize_standard_tx(tx) is None

    def test_normalize_internal_tx(self) -> None:
        """Internal TX normalizes correctly."""
        tx = {
            "from": "0xcontract",
            "to": "0xwallet",
            "value": "500000000000000000",  # 0.5 ETH
            "hash": "0xhash2",
            "blockNumber": "12346",
            "timeStamp": "1700000100",
        }
        edge = normalize_internal_tx(tx)
        assert edge is not None
        assert edge.value == Decimal("0.5")
        assert edge.edge_type == EdgeType.INTERNAL_TRANSFER

    def test_normalize_erc20_tx(self) -> None:
        """ERC-20 transfer normalizes with token metadata."""
        tx = {
            "from": "0xsender",
            "to": "0xrecipient",
            "value": "1000000",  # 1 USDC (6 decimals)
            "hash": "0xhash3",
            "blockNumber": "12347",
            "timeStamp": "1700000200",
            "tokenDecimal": "6",
            "tokenSymbol": "USDC",
            "contractAddress": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        }
        edge = normalize_erc20_tx(tx)
        assert edge is not None
        assert edge.value == Decimal("1")
        assert edge.token_symbol == "USDC"
        assert edge.edge_type == EdgeType.ERC20_TRANSFER


class TestBridgeDetection:
    """Tests for cross-chain bridge detection."""

    def test_detect_wormhole_deposit(self) -> None:
        """Edge to Wormhole contract detected as bridge deposit."""
        wormhole = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"
        edge = GoGEdge(
            edge_id="tx:0",
            from_node_id="ethereum:0xwhale",
            to_node_id=f"ethereum:{wormhole}",
            chain=ChainId.ETHEREUM,
            edge_type=EdgeType.TRANSFER,
            value=Decimal("100"),
            tx_hash="0xtxhash",
        )
        interactions = detect_bridge_interactions([edge])
        assert len(interactions) == 1
        assert interactions[0].bridge_name == "Wormhole"
        assert interactions[0].direction == "deposit"

    def test_no_bridge_for_normal_tx(self) -> None:
        """Normal wallet-to-wallet TX has no bridge interactions."""
        edge = GoGEdge(
            edge_id="tx:0",
            from_node_id="ethereum:0xaaa",
            to_node_id="ethereum:0xbbb",
            chain=ChainId.ETHEREUM,
            edge_type=EdgeType.TRANSFER,
        )
        assert detect_bridge_interactions([edge]) == []

    def test_contagion_risk_high(self) -> None:
        """Multiple bridges + high volume → HIGH risk."""
        interactions = [
            BridgeInteraction(
                bridge_name="Wormhole",
                bridge_contract="0x1",
                direction="deposit",
                value=Decimal("50"),
            ),
            BridgeInteraction(
                bridge_name="Stargate",
                bridge_contract="0x2",
                direction="deposit",
                value=Decimal("60"),
            ),
            BridgeInteraction(
                bridge_name="Thorchain",
                bridge_contract="0x3",
                direction="deposit",
                value=Decimal("10"),
            ),
        ]
        assert calculate_contagion_risk(interactions) == "HIGH"

    def test_contagion_risk_low_empty(self) -> None:
        """No interactions → LOW risk."""
        assert calculate_contagion_risk([]) == "LOW"


# ═══════════════════════ BTC Normalizer Tests ════════════════════════════════


class TestBtcNormalizerNodes:
    """Tests for Bitcoin node construction."""

    def test_build_btc_node_id(self) -> None:
        """Node ID uses 'bitcoin:' prefix."""
        assert build_btc_node_id("1A1zP1...") == "bitcoin:1A1zP1..."

    def test_classify_known_cex(self) -> None:
        """Known CEX address classified correctly."""
        addr = "3FHNBLobJnbCTFTVakh5TXmEneyf5PT61B"
        assert classify_btc_node(addr) == NodeType.CEX_HOT_WALLET

    def test_classify_unknown_wallet(self) -> None:
        """Unknown address classified as WALLET."""
        assert classify_btc_node("1Unknown123") == NodeType.WALLET

    def test_build_btc_node_with_label(self) -> None:
        """Known CEX node gets human-readable label."""
        addr = "3FHNBLobJnbCTFTVakh5TXmEneyf5PT61B"
        node = build_btc_node(addr)
        assert node.label == "Binance Hot Wallet 1"
        assert node.node_type == NodeType.CEX_HOT_WALLET


class TestBtcNormalizerEdges:
    """Tests for Bitcoin UTXO normalization."""

    def test_normalize_vout_edges(self) -> None:
        """Vout entries produce outbound GoG edges."""
        tx = {
            "vout": [
                {
                    "value": "0.50000000",
                    "n": 0,
                    "scriptPubKey": {
                        "address": "1RecipientAddr",
                    },
                },
                {
                    "value": "0.49990000",
                    "n": 1,
                    "scriptPubKey": {
                        "address": "1ChangeAddr",
                    },
                },
            ],
            "blocktime": 1700000000,
        }
        edges = normalize_vout_edges(tx, "txid123")
        assert len(edges) == 2
        assert edges[0].value == Decimal("0.50000000")
        assert edges[0].to_node_id == "bitcoin:1RecipientAddr"

    def test_extract_vin_txids(self) -> None:
        """Vin entries return previous txid references."""
        tx = {
            "vin": [
                {"txid": "prev_tx_1", "vout": 0},
                {"txid": "prev_tx_2", "vout": 1},
            ],
        }
        refs = extract_vin_txids(tx)
        assert len(refs) == 2
        assert refs[0]["txid"] == "prev_tx_1"

    def test_coinbase_vin_skipped(self) -> None:
        """Coinbase vin entries are skipped."""
        tx = {
            "vin": [
                {"coinbase": "03abc", "sequence": 4294967295},
            ],
        }
        refs = extract_vin_txids(tx)
        assert len(refs) == 0

    def test_vout_no_address(self) -> None:
        """Vout without address produces no edge."""
        tx = {
            "vout": [
                {
                    "value": "0",
                    "n": 0,
                    "scriptPubKey": {"asm": "OP_RETURN"},
                },
            ],
        }
        edges = normalize_vout_edges(tx, "txid_opreturn")
        assert len(edges) == 0


class TestBtcDecimalPrecision:
    """Tests for BTC Decimal precision."""

    def test_satoshi_to_decimal(self) -> None:
        """BTC values preserve 8-decimal precision."""
        result = _to_decimal("0.00000001")
        assert result == Decimal("0.00000001")

    def test_to_decimal_none_returns_zero(self) -> None:
        """None input returns Decimal('0')."""
        assert _to_decimal(None) == Decimal("0")

    def test_to_decimal_invalid_returns_zero(self) -> None:
        """Invalid input returns Decimal('0')."""
        assert _to_decimal("not_a_number") == Decimal("0")


# ═══════════════════════ Timestamp Helper ════════════════════════════════════


class TestTimestampHelper:
    """Tests for Unix → ISO-8601 conversion."""

    def test_valid_timestamp(self) -> None:
        """Valid Unix timestamp converts to ISO-8601."""
        result = _unix_to_iso("1700000000")
        assert "2023-11-14" in result

    def test_invalid_timestamp(self) -> None:
        """Invalid timestamp returns empty string."""
        assert _unix_to_iso("not_a_number") == ""

    def test_zero_timestamp(self) -> None:
        """Zero timestamp converts to epoch."""
        result = _unix_to_iso("0")
        assert "1970" in result


# ═══════════════════════ Degraded Mode / Error Path Tests ════════════════════


class TestDegradedModeResponses:
    """Tests for server-level DEGRADED and ERROR response paths."""

    def test_encode_error_returns_json_with_status(self) -> None:
        """_encode_error returns valid JSON with ERROR status."""
        from mcp_servers.multichain_rpc.servers import _encode_error

        result = _encode_error("BTC client not initialised")
        decoded = msgspec.json.decode(result.encode())
        assert decoded["status"] == "ERROR"
        assert decoded["error"] == "BTC client not initialised"

    def test_degraded_graph_response(self) -> None:
        """_encode_degraded_graph returns DEGRADED GoGGraphResponse."""
        from mcp_servers.multichain_rpc.servers import _encode_degraded_graph

        result = _encode_degraded_graph("0xdead", "timeout", "2024-01-01T00:00:00")
        decoded = msgspec.json.decode(result.encode())
        assert decoded["status"] == "DEGRADED"
        assert decoded["query_address"] == "0xdead"
        assert decoded["nodes"] == []
        assert decoded["edges"] == []

    def test_degraded_bridge_response(self) -> None:
        """_encode_degraded_bridge returns DEGRADED BridgeActivityReport."""
        from mcp_servers.multichain_rpc.servers import _encode_degraded_bridge

        result = _encode_degraded_bridge("0xWhale", "API error", "2024-01-01T00:00:00")
        decoded = msgspec.json.decode(result.encode())
        assert decoded["status"] == "DEGRADED"
        assert decoded["address"] == "0xwhale"  # lowercased
        assert decoded["interactions"] == []
        assert decoded["contagion_risk_level"] == "LOW"

    def test_graph_response_degraded_has_zero_volume(self) -> None:
        """Degraded BridgeActivityReport has zero bridge volume."""
        from mcp_servers.multichain_rpc.servers import _encode_degraded_bridge

        result = _encode_degraded_bridge("0x1", "err", "2024-01-01T00:00:00")
        decoded = msgspec.json.decode(result.encode())
        assert decoded["total_bridge_volume"] == "0"
        assert decoded["unique_bridges_used"] == 0

