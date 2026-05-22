"""Load ATLAS production evaluation packets (v3) from bundled markdown.

The markdown file mirrors ``ATLASPROMPTS/atlas_production_packets_v3.md``:
one JSON object per line, suitable as a single user message after system prompt v3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import msgspec

_PRODUCTION_PACKETS_V3_FILENAME = "atlas_production_packets_v3.md"


def production_packets_v3_path() -> Path:
    """Absolute path to the bundled ``atlas_production_packets_v3.md``."""
    return Path(__file__).resolve().parent / _PRODUCTION_PACKETS_V3_FILENAME


def iter_production_packet_json_lines(markdown_text: str | None = None) -> Iterator[str]:
    """Yield raw JSON lines (root object) from the v3 markdown body."""
    text = markdown_text if markdown_text is not None else production_packets_v3_path().read_text(
        encoding="utf-8",
    )
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            yield stripped


def load_production_packets_v3() -> list[dict[str, Any]]:
    """Decode all v3 packets as dicts (standard snapshots and ``position_monitor``)."""
    result: list[dict[str, Any]] = []
    for line in iter_production_packet_json_lines():
        decoded = msgspec.json.decode(line.encode("utf-8"))
        if not isinstance(decoded, dict):
            raise TypeError("production packet root must be a JSON object")
        result.append(decoded)
    return result


def encode_packet_user_message_line(packet: dict[str, Any]) -> str:
    """Single-line JSON for LM Studio user message (canonical re-encoding)."""
    body = msgspec.json.encode(packet)
    return body.decode("utf-8")
