"""Redis Stream Payload Schemas.

Declares the durable shape of payloads in ingestion streams.
Uses msgspec for fast serialization, ensuring precise Decimal
representation by serializing as strings.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal, TYPE_CHECKING

import msgspec

if TYPE_CHECKING:
    from atlas.providers.hydra.listener import HydraCascadeEvent


class HydraCascadeStreamPayload(msgspec.Struct, frozen=True):
    """Durable shape of HYDRA cascade events in the ingestion stream."""

    event_id: str
    asset: str
    tier: Literal[1, 2, 3, 4]
    exchanges: list[str]
    total_liquidation_usd_str: str
    timestamp_iso: str

    def to_domain(self) -> "HydraCascadeEvent":
        """Convert to domain model (with Decimal hydration)."""
        from atlas.providers.hydra.listener import HydraCascadeEvent
        return HydraCascadeEvent(
            event_id=self.event_id,
            asset=self.asset,
            tier=self.tier,
            exchanges=self.exchanges,
            total_liquidation_usd=Decimal(self.total_liquidation_usd_str),
            timestamp=datetime.fromisoformat(self.timestamp_iso),
        )


class HydraPriceStreamPayload(msgspec.Struct, frozen=True):
    """Durable shape of HYDRA price events in the ingestion stream."""

    asset: str
    price_str: str
    source_exchange: str
    timestamp_iso: str


class DefillamaTvlStreamPayload(msgspec.Struct, frozen=True):
    """Durable shape of DeFi Llama TVL events in the ingestion stream."""

    protocol: str
    chain: str
    tvl_usd_str: str
    timestamp_iso: str
