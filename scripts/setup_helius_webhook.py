"""One-time Helius webhook registration for exchange address monitoring.

Usage (after FastAPI is reachable publicly or via ngrok):

    export HELIUS_WEBHOOK_URL=https://your-host/api/webhooks/helius
    python -m scripts.setup_helius_webhook
"""

from __future__ import annotations

import asyncio
import sys

from atlas.core.registry import helius_provider, initialize_registry
from atlas.shared.config import PolarisSettings


async def main() -> None:
    settings = PolarisSettings()
    api_key = settings.helius_api_key.get_secret_value()
    if not api_key:
        print("ERROR: Set HELIUS_API_KEY in environment")
        sys.exit(1)

    webhook_url = settings.helius_webhook_url.strip()
    if not webhook_url:
        print("ERROR: Set HELIUS_WEBHOOK_URL to your public POST /api/webhooks/helius endpoint")
        sys.exit(1)

    import httpx
    import redis.asyncio as redis_async

    redis_client = redis_async.from_url(
        settings.redis_url,
        decode_responses=False,
        socket_timeout=5.0,
    )
    http_client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), http2=True)
    initialize_registry(settings, redis_client)

    helius = helius_provider
    if helius is None:
        print("ERROR: Helius provider failed to initialize")
        sys.exit(1)

    print(f"Registering webhook at: {webhook_url}")
    existing = await helius.list_webhooks()
    if existing:
        print(f"Found {len(existing)} existing webhooks:")
        for wh in existing:
            print(f"  - {wh.get('webhookID')}: {wh.get('webhookURL')}")

    result = await helius.create_exchange_flow_webhook(webhook_url)
    await http_client.aclose()

    if not result:
        print("ERROR: Failed to create webhook")
        sys.exit(1)

    print(f"Webhook created: {result.get('webhookID')}")
    addrs = result.get("accountAddresses", [])
    print(f"Monitoring {len(addrs) if isinstance(addrs, list) else 0} exchange addresses")


if __name__ == "__main__":
    asyncio.run(main())
