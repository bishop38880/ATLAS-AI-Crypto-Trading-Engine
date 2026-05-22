"""Canonical Bitget V2 HMAC-SHA256 request signing.

This is the ONE shared module between the kill switch and the
execution client.  Both import from here.  Do NOT duplicate
signing logic anywhere else in the codebase.

Bitget V2 signature spec:
    message  = timestamp_ms + METHOD + requestPath + body
    signature = base64( HMAC-SHA256(api_secret, message) )

Headers:
    ACCESS-KEY, ACCESS-SIGN, ACCESS-TIMESTAMP, ACCESS-PASSPHRASE,
    Content-Type: application/json, locale: en-US
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


def _current_timestamp_ms() -> str:
    """Millisecond-precision Unix timestamp as string."""
    return str(int(time.time() * 1000))


def sign_request(
    api_secret: str,
    timestamp_ms: str,
    method: str,
    path: str,
    body: bytes = b"",
) -> str:
    """Compute HMAC-SHA256 signature for a Bitget V2 REST request.

    Args:
        api_secret: The API secret used for HMAC signing.
        timestamp_ms: Millisecond timestamp string.
        method: HTTP method in UPPERCASE (GET, POST).
        path: Full request path including query string.
        body: Request body bytes (empty for GET requests).

    Returns:
        Base64-encoded HMAC-SHA256 signature string.
    """
    message = timestamp_ms + method.upper() + path
    if body:
        message += body.decode("utf-8")
    mac = hmac.new(
        api_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(mac.digest()).decode("utf-8")


def build_signed_headers(
    api_key: str,
    api_secret: str,
    api_passphrase: str,
    method: str,
    path: str,
    body: bytes = b"",
) -> dict[str, str]:
    """Build a complete Bitget V2 authentication header dict.

    Args:
        api_key: Bitget API key.
        api_secret: Bitget API secret for HMAC signing.
        api_passphrase: Bitget API passphrase.
        method: HTTP method (GET / POST).
        path: Request path with query string.
        body: Request body bytes.

    Returns:
        Dict with all required Bitget auth headers.
    """
    ts = _current_timestamp_ms()
    signature = sign_request(api_secret, ts, method, path, body)
    return {
        "ACCESS-KEY": api_key,
        "ACCESS-SIGN": signature,
        "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": api_passphrase,
        "Content-Type": "application/json",
        "locale": "en-US",
    }
