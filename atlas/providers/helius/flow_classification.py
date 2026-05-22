"""Classify Helius enhanced transactions into flow events."""

from __future__ import annotations

import time
from typing import Any

from atlas.providers.helius.models import FlowEvent
from atlas.providers.helius.solana_addresses import (
    ADDRESS_TO_EXCHANGE,
    ALL_EXCHANGE_ADDRESSES,
    JUP_TOKEN_MINT,
    WEBHOOK_MIN_SOL,
)


def classify_polled_transaction(
    tx: dict[str, Any],
    watched_address: str,
    exchange: str,
    sol_price_usd: float,
    symbol: str = "SOL",
) -> FlowEvent | None:
    """Classify a polled Helius enhanced transaction for one exchange address."""
    try:
        native_transfers = tx.get("nativeTransfers", [])
        if not native_transfers:
            return None

        signature = str(tx.get("signature", ""))
        timestamp = float(tx.get("timestamp", time.time()))
        total_in = 0.0
        total_out = 0.0

        for transfer in native_transfers:
            amount_sol = float(transfer.get("amount", 0)) / 1e9
            if amount_sol < WEBHOOK_MIN_SOL:
                continue
            to_addr = str(transfer.get("toUserAccount", ""))
            from_addr = str(transfer.get("fromUserAccount", ""))
            if to_addr == watched_address:
                total_in += amount_sol
            elif from_addr == watched_address:
                total_out += amount_sol

        net_sol = total_in - total_out
        if abs(net_sol) < WEBHOOK_MIN_SOL:
            return None

        amount_usd = abs(net_sol) * sol_price_usd
        if net_sol > 0:
            direction: str = "inflow"
            from_addr = str(native_transfers[0].get("fromUserAccount", "unknown"))
            to_addr = watched_address
        else:
            direction = "outflow"
            from_addr = watched_address
            to_addr = str(native_transfers[0].get("toUserAccount", "unknown"))

        return FlowEvent(
            timestamp=timestamp,
            symbol=symbol.upper(),
            direction=direction,  # type: ignore[arg-type]
            amount_usd=amount_usd,
            amount_native=abs(net_sol),
            source="poll",
            exchange=exchange,
            from_address=from_addr,
            to_address=to_addr,
            signature=signature,
        )
    except Exception:
        return None


def extract_webhook_flow_events(
    tx: dict[str, Any],
    sol_price_usd: float,
    jup_price_usd: float,
) -> list[FlowEvent]:
    """Extract flow events from one Helius webhook enhanced transaction."""
    events: list[FlowEvent] = []
    signature = str(tx.get("signature", ""))
    timestamp = float(tx.get("timestamp", time.time()))

    for transfer in tx.get("nativeTransfers", []):
        amount_sol = float(transfer.get("amount", 0)) / 1e9
        if amount_sol < WEBHOOK_MIN_SOL:
            continue

        from_addr = str(transfer.get("fromUserAccount", ""))
        to_addr = str(transfer.get("toUserAccount", ""))
        amount_usd = amount_sol * sol_price_usd
        direction = None
        exchange = None

        if to_addr in ALL_EXCHANGE_ADDRESSES:
            direction = "inflow"
            exchange = ADDRESS_TO_EXCHANGE.get(to_addr, "unknown")
        elif from_addr in ALL_EXCHANGE_ADDRESSES:
            direction = "outflow"
            exchange = ADDRESS_TO_EXCHANGE.get(from_addr, "unknown")
        else:
            continue

        events.append(
            FlowEvent(
                timestamp=timestamp,
                symbol="SOL",
                direction=direction,  # type: ignore[arg-type]
                amount_usd=amount_usd,
                amount_native=amount_sol,
                source="webhook",
                exchange=exchange,
                from_address=from_addr,
                to_address=to_addr,
                signature=f"{signature}:{from_addr[:8]}:{to_addr[:8]}",
            ),
        )

    for transfer in tx.get("tokenTransfers", []):
        token_amount = float(transfer.get("tokenAmount", 0))
        mint = str(transfer.get("mint", ""))
        if mint != JUP_TOKEN_MINT:
            continue

        from_addr = str(transfer.get("fromUserAccount", ""))
        to_addr = str(transfer.get("toUserAccount", ""))
        direction = None
        exchange = None

        if to_addr in ALL_EXCHANGE_ADDRESSES:
            direction = "inflow"
            exchange = ADDRESS_TO_EXCHANGE.get(to_addr, "unknown")
        elif from_addr in ALL_EXCHANGE_ADDRESSES:
            direction = "outflow"
            exchange = ADDRESS_TO_EXCHANGE.get(from_addr, "unknown")
        else:
            continue

        amount_usd = token_amount * jup_price_usd
        events.append(
            FlowEvent(
                timestamp=timestamp,
                symbol="JUP",
                direction=direction,  # type: ignore[arg-type]
                amount_usd=amount_usd,
                amount_native=token_amount,
                source="webhook",
                exchange=exchange,
                from_address=from_addr,
                to_address=to_addr,
                signature=f"{signature}:{mint[:8]}:{from_addr[:8]}",
            ),
        )

    return events
