"""OpenAI-compatible chat completions via httpx (LM Studio, DeepSeek, Mistral)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import msgspec
from loguru import logger
from pydantic import SecretStr

from atlas.shared.config import PolarisSettings


def lmstudio_chat_url(settings: PolarisSettings) -> str:
    base = settings.lmstudio_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return "{}/chat/completions".format(base)


def deepseek_chat_url(settings: PolarisSettings) -> str:
    base = settings.deepseek_base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return "{}/v1/chat/completions".format(base)


def mistral_chat_url() -> str:
    return "{}/chat/completions".format(
        "https://api.mistral.ai/v1".rstrip("/"),
    )


def lmstudio_bearer(settings: PolarisSettings) -> str:
    for candidate in (
        settings.embed_api_key.get_secret_value(),
        settings.deepseek_api_key.get_secret_value(),
    ):
        trimmed = candidate.strip()
        if trimmed:
            return trimmed
    return "lm-studio"


async def fetch_chat_json(
    *,
    http_client: httpx.AsyncClient,
    url: str,
    authorization: str,
    model: str,
    system_prompt: str,
    user_content: str,
    max_tokens: int,
    timeout_s: float,
) -> dict[str, Any]:
    """POST chat/completions and parse the assistant message as JSON."""
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": "Bearer {}".format(authorization),
        "Content-Type": "application/json",
    }
    try:
        response = await asyncio.wait_for(
            http_client.post(
                url,
                content=msgspec.json.encode(body),
                headers=headers,
            ),
            timeout=timeout_s,
        )
        response.raise_for_status()
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError as exc:
        raise TimeoutError("chat completion timed out after {}s".format(timeout_s)) from exc
    except httpx.HTTPStatusError as exc:
        logger.error(
            "chat_http_error | status={} | body={}",
            exc.response.status_code,
            exc.response.text[:200],
        )
        raise
    except Exception as exc:
        logger.error("chat_request_failed | err={}", str(exc))
        raise

    data = msgspec.json.decode(response.content, type=dict)
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("chat response missing choices")
    message = choices[0].get("message") or {}
    raw_text = message.get("content") or ""
    if not raw_text.strip():
        raise ValueError("chat response empty content")
    return msgspec.json.decode(raw_text.encode("utf-8"), type=dict)


def secret_or_empty(secret: SecretStr) -> str:
    return secret.get_secret_value().strip()
