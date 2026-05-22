"""Helius webhook receiver — real-time Solana exchange flow events."""

from __future__ import annotations

import asyncio
from typing import Any

import msgspec
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from loguru import logger

from atlas.providers.helius.flow_classification import extract_webhook_flow_events
from atlas.providers.helius.price_cache import fetch_sol_jup_prices_usd
from atlas.providers.helius.solana_addresses import (
    LARGE_TX_THRESHOLD_USD,
    WHALE_TX_THRESHOLD_USD,
)
from atlas.services import solana_flow_tracker
from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


def _verify_webhook_secret(request: Request, settings: PolarisSettings) -> None:
    secret = settings.helius_webhook_secret.get_secret_value()
    if not secret:
        return
    auth_header = request.headers.get("Authorization", "")
    if auth_header not in (secret, f"Bearer {secret}"):
        raise HTTPException(status_code=401, detail="Invalid webhook secret")


@router.post("/helius")
async def receive_helius_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str | int]:
    """Accept Helius enhanced transaction batches (responds immediately)."""
    settings = PolarisSettings()
    _verify_webhook_secret(request, settings)

    try:
        body = await request.body()
        payload: Any = msgspec.json.decode(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from None

    transactions = payload if isinstance(payload, list) else [payload]
    if not isinstance(transactions, list):
        raise HTTPException(status_code=400, detail="Expected transaction array")

    http_client = getattr(request.app.state, "dashboard_market_http", None)
    background_tasks.add_task(_process_webhook_batch, transactions, http_client)
    return {"status": "accepted", "count": len(transactions)}


async def _process_webhook_batch(
    transactions: list[Any],
    http_client: Any,
) -> None:
    if http_client is None:
        logger.warning("helius_webhook_no_http_client")
        return

    sol_price, jup_price = await fetch_sol_jup_prices_usd(http_client)
    if sol_price is None:
        logger.warning("helius_webhook_skip_batch | reason=no_sol_price")
        return
    jup_price_usd = jup_price if jup_price is not None else 0.0

    recorded = 0
    for tx in transactions:
        if not isinstance(tx, dict):
            continue
        try:
            events = extract_webhook_flow_events(tx, sol_price, jup_price_usd)
            for event in events:
                if event.amount_usd < WHALE_TX_THRESHOLD_USD:
                    continue
                if await solana_flow_tracker.record_flow_event(event):
                    recorded += 1
                if event.amount_usd >= LARGE_TX_THRESHOLD_USD:
                    logger.warning(
                        "helius_webhook_large_tx | symbol={} | direction={} | usd={} | exchange={}",
                        event.symbol,
                        event.direction,
                        round(event.amount_usd, 2),
                        event.exchange or "n/a",
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("helius_webhook_tx_parse_failed | err={}", exc)

    if recorded > 0:
        logger.info(
            "helius_webhook_batch_done | recorded={} | batch_size={}",
            recorded,
            len(transactions),
        )
