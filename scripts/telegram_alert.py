"""Lightweight Telegram alerting for chaos drill failures.

Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_CHAT_ID`` from env.
Fire-and-forget — logs failure but never raises.
"""

from __future__ import annotations

import os

import httpx
from loguru import logger

_TELEGRAM_API = "https://api.telegram.org/bot{}/sendMessage"
_TIMEOUT_S: int = 10


async def send_telegram_alert(message: str) -> bool:
    """Send alert via Telegram Bot API.

    Args:
        message: The alert text to send.

    Returns:
        True on success, False on failure.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        logger.warning("telegram not configured — skipping alert")
        return False

    url = _TELEGRAM_API.format(token)
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                logger.info("telegram_alert_sent | chat_id={}", chat_id)
                return True
            logger.error(
                "telegram_alert_failed | status={} | body={}",
                resp.status_code,
                resp.text[:200],
            )
            return False
    except Exception as exc:
        logger.error("telegram_alert_exception | exc={}", exc)
        return False
