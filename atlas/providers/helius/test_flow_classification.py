"""Tests for Helius transaction flow classification."""

from __future__ import annotations

import time

from atlas.providers.helius.flow_classification import (
    classify_polled_transaction,
    extract_webhook_flow_events,
)
from atlas.providers.helius.solana_addresses import WHALE_TX_THRESHOLD_USD


_BINANCE_HOT = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"


def _native_tx(
    *,
    signature: str = "sig_test",
    to_addr: str = _BINANCE_HOT,
    from_addr: str = "whale_sender",
    lamports: int = 600_000_000_000,
) -> dict:
    return {
        "signature": signature,
        "timestamp": time.time(),
        "nativeTransfers": [
            {
                "fromUserAccount": from_addr,
                "toUserAccount": to_addr,
                "amount": lamports,
            },
        ],
    }


def test_classify_polled_inflow_to_exchange() -> None:
    tx = _native_tx()
    event = classify_polled_transaction(tx, _BINANCE_HOT, "binance", 150.0, "SOL")
    assert event is not None
    assert event.direction == "inflow"
    assert event.amount_usd >= WHALE_TX_THRESHOLD_USD


def test_extract_webhook_skips_non_exchange_transfer() -> None:
    tx = _native_tx(to_addr="random_wallet", from_addr="another_wallet")
    events = extract_webhook_flow_events(tx, 150.0, 1.0)
    assert events == []


def test_extract_webhook_exchange_outflow() -> None:
    tx = _native_tx(
        from_addr=_BINANCE_HOT,
        to_addr="cold_storage_wallet",
    )
    events = extract_webhook_flow_events(tx, 150.0, 1.0)
    assert len(events) == 1
    assert events[0].direction == "outflow"
    assert events[0].symbol == "SOL"
