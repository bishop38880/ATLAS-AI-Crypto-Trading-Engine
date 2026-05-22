"""HTTP panic endpoint — FastAPI route for emergency manual halt.

Provides ``POST /kill-switch/halt`` with:
    - ``X-Panic-Key`` header authentication (HMAC-safe).
    - IP-based rate limiting (3 attempts per minute).
    - No detail leakage on failure (HTTP 403).

Rate limiting is implemented in-memory — no external dependency.
"""

import time
from collections import defaultdict

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger

from prometheus.kill_switch.core import KillSwitch

router = APIRouter(prefix="/kill-switch", tags=["kill-switch"])

# In-memory rate limiter: IP → list of attempt timestamps.
_rate_limits: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS_PER_MINUTE = 3
_WINDOW_SECONDS = 60.0


def _check_rate_limit(client_ip: str) -> bool:
    """Return ``True`` if the client is within the rate limit.

    Prunes stale entries older than the window on each call.
    """
    now = time.monotonic()
    attempts = _rate_limits[client_ip]
    _rate_limits[client_ip] = [
        ts for ts in attempts if now - ts < _WINDOW_SECONDS
    ]
    return len(_rate_limits[client_ip]) < _MAX_ATTEMPTS_PER_MINUTE


def _record_attempt(client_ip: str) -> None:
    """Record a rate-limit attempt for the given IP."""
    _rate_limits[client_ip].append(time.monotonic())


def create_halt_route(kill_switch: KillSwitch) -> APIRouter:
    """Build and return the halt router bound to the given kill switch.

    Args:
        kill_switch: The ``KillSwitch`` instance to invoke on panic.

    Returns:
        Configured ``APIRouter`` with the halt endpoint.
    """

    @router.post("/halt")
    async def halt_endpoint(
        request: Request,
        x_panic_key: str = Header(..., alias="X-Panic-Key"),
    ) -> dict[str, str]:
        """Emergency halt endpoint — requires ``X-Panic-Key`` header."""
        client_ip = _extract_client_ip(request)
        _enforce_rate_limit(client_ip)
        _record_attempt(client_ip)

        if not kill_switch.verify_panic_key(x_panic_key):
            logger.warning("invalid panic key attempt | client_ip={}", client_ip)
            raise HTTPException(status_code=403, detail="forbidden")

        await kill_switch.halt(
            reason="MANUAL_PANIC_KEY",
            triggered_by=client_ip,
            details={"source": "http_endpoint"},
        )
        return {"status": "halted", "triggered_by": client_ip}

    return router


def _extract_client_ip(request: Request) -> str:
    """Extract the client IP from the request."""
    return request.client.host if request.client else "unknown"


def _enforce_rate_limit(client_ip: str) -> None:
    """Raise HTTP 429 if the client has exceeded the rate limit."""
    if not _check_rate_limit(client_ip):
        logger.warning(
            "rate limit exceeded for panic endpoint | client_ip={}",
            client_ip,
        )
        raise HTTPException(status_code=429, detail="rate limit exceeded")

