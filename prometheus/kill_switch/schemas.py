"""Kill switch event schemas — msgspec structs for halt/resume events.

Published to canonical ``prometheus:system_halt`` Redis Pub/Sub channel.
Consumed by the reconciler (Session 14), dashboard, and any subscriber
that must react to emergency trading halts.
"""

from typing import Literal

import msgspec


class SystemHaltEvent(msgspec.Struct, frozen=True):
    """Canonical halt/resume event broadcast on ``prometheus:system_halt``.

    Attributes:
        event_type: Whether this is a HALT or RESUME event.
        reason: Machine-readable reason code for the event.
        triggered_by: Identifier of the actor that triggered the event
            (e.g. IP address, agent name, ``human_operator``).
        timestamp_iso: ISO-8601 UTC timestamp of when the event occurred.
        details: Arbitrary key-value metadata about the event.
    """

    event_type: Literal["HALT", "RESUME"]
    reason: Literal[
        "MANUAL_PANIC_KEY",
        "SPIKE_DETECTOR",
        "POSITION_LIMIT_BREACH",
        "API_FAILURE_STORM",
        "RECONCILER_MISMATCH",
        "LIQUIDATION_PROXIMITY",
        "MANUAL_RESUME",
    ]
    triggered_by: str
    timestamp_iso: str
    details: dict[str, str | int | float]
