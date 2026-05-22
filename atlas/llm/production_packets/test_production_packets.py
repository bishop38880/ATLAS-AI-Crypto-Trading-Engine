"""Tests for bundled LM Studio production packet fixtures (v3)."""

from __future__ import annotations

import msgspec

from atlas.llm.production_packets.loader import (
    encode_packet_user_message_line,
    iter_production_packet_json_lines,
    load_production_packets_v3,
    production_packets_v3_path,
)

EXPECTED_ROOT_IDS: tuple[str, ...] = (
    "prod-001-wif-squeeze",
    "prod-002-strk-deadzone",
    "prod-003-aave-longsqueeze",
    "prod-004-pepe-trap",
    "prod-005-crv-weakbuy",
    "pm-001-wif-hold",
    "pm-002-sol-exit",
    "pm-003-inj-stage2",
    "prod-009-avax-contradiction",
    "prod-010-near-stale",
)


class TestProductionPacketsV3:
    """Decode and structural checks for atlas_production_packets_v3.md."""

    def test_markdown_file_exists(self) -> None:
        """Bundled markdown is present next to the loader."""
        path = production_packets_v3_path()
        assert path.is_file()
        assert "PACKET 1" in path.read_text(encoding="utf-8")

    def test_yields_ten_json_lines(self) -> None:
        """Exactly ten root JSON objects in the fixture."""
        lines = list(iter_production_packet_json_lines())
        assert len(lines) == 10

    def test_load_decoded_order_and_ids(self) -> None:
        """Packet order and ids match the eval golden list."""
        packets = load_production_packets_v3()
        assert len(packets) == 10
        for packet, expected_id in zip(packets, EXPECTED_ROOT_IDS, strict=True):
            assert packet["id"] == expected_id

    def test_position_monitor_packets(self) -> None:
        """Packets 6–8 declare position_monitor type."""
        packets = load_production_packets_v3()
        monitors = [p for p in packets if p.get("type") == "position_monitor"]
        assert len(monitors) == 3
        assert {"pos", "entry_context", "delta"}.issubset(monitors[0].keys())

    def test_standard_packets_have_timeframe(self) -> None:
        """Non-monitor packets use tf; monitors use tf_monitor / tf_entry."""
        packets = load_production_packets_v3()
        for packet in packets:
            if packet.get("type") == "position_monitor":
                assert "tf_monitor" in packet
                assert "tf_entry" in packet
            else:
                assert "tf" in packet

    def test_round_trip_reencode(self) -> None:
        """Re-encoding preserves JSON semantics (msgspec round-trip)."""
        packets = load_production_packets_v3()
        first = packets[0]
        line = encode_packet_user_message_line(first)
        again = msgspec.json.decode(line.encode("utf-8"))
        assert isinstance(again, dict)
        assert again["id"] == first["id"]
        assert again["sym"] == first["sym"]
