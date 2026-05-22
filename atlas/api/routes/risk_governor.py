"""Risk Governor REST — portfolio safety telemetry and emergency halt."""

from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from atlas.api.risk_governor_logic import build_risk_governor_snapshot
from atlas.api.schemas import RiskGovernorSnapshot
from atlas.shared.config import PolarisSettings
from prometheus.kill_switch.core import KillSwitch

router = APIRouter(prefix="/api/risk-governor", tags=["risk-governor"])

_BaseConfig = ConfigDict(
    frozen=True,
    populate_by_name=True,
    alias_generator=to_camel,
    extra="forbid",
)

_rate_limits: dict[str, list[float]] = defaultdict(list)
_MAX_ATTEMPTS_PER_MINUTE = 3
_WINDOW_SECONDS = 60.0


def _audit_log_path() -> Path:
    path = Path(__file__).resolve().parents[3] / "var" / "kill_switch_audit.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _check_rate_limit(client_ip: str) -> bool:
    now = time.monotonic()
    attempts = _rate_limits[client_ip]
    _rate_limits[client_ip] = [ts for ts in attempts if now - ts < _WINDOW_SECONDS]
    return len(_rate_limits[client_ip]) < _MAX_ATTEMPTS_PER_MINUTE


def _record_attempt(client_ip: str) -> None:
    _rate_limits[client_ip].append(time.monotonic())


def _extract_client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class KillSwitchHaltBody(BaseModel):
    model_config = _BaseConfig
    panic_key: str = Field(min_length=1, validation_alias="panicKey")


class StagedExitBody(BaseModel):
    model_config = _BaseConfig

    order_id: str = Field(min_length=1, validation_alias="orderId")
    symbol: str = Field(min_length=1)
    direction: str = Field(min_length=1, description="LONG or SHORT")
    close_pct: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        validation_alias="closePct",
    )
    reason: str = Field(default="risk_governor_staged_exit", min_length=1)


@router.get("/snapshot", response_model=RiskGovernorSnapshot, response_model_by_alias=True)
async def get_risk_governor_snapshot(request: Request) -> RiskGovernorSnapshot:
    """Return live Risk Manager / portfolio gate state for the operator dashboard."""
    redis = request.app.state.redis
    settings = PolarisSettings()
    return await build_risk_governor_snapshot(redis, settings)


@router.post("/kill-switch/halt")
async def post_kill_switch_halt(
    request: Request,
    x_panic_key: str | None = Header(default=None, alias="X-Panic-Key"),
    body: KillSwitchHaltBody | None = None,
) -> dict[str, str]:
    """Emergency trading halt — forwards to the Prometheus kill switch."""
    client_ip = _extract_client_ip(request)
    if not _check_rate_limit(client_ip):
        logger.warning("risk_governor_kill_switch_rate_limited | ip={}", client_ip)
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    _record_attempt(client_ip)

    key = (x_panic_key or "").strip() or (body.panic_key if body else "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="panic_key_required")

    redis = request.app.state.redis
    kill_switch = KillSwitch(redis_client=redis, audit_log_path=_audit_log_path())
    if not kill_switch.verify_panic_key(key):
        logger.warning("risk_governor_kill_switch_forbidden | ip={}", client_ip)
        raise HTTPException(status_code=403, detail="forbidden")

    await kill_switch.halt(
        reason="MANUAL_PANIC_KEY",
        triggered_by=client_ip,
        details={"source": "risk_governor_dashboard"},
    )
    return {"status": "halted", "triggered_by": client_ip}


@router.post("/staged-exit")
async def post_staged_exit(
    request: Request,
    body: StagedExitBody,
) -> dict[str, str]:
    """Publish a partial or full market close to PROMETHEUS (ATLAS never hits the exchange)."""
    from backend.pipeline.staged_exit import PrometheusStagedExitBridge

    direction = body.direction.strip().upper()
    if direction not in ("LONG", "SHORT"):
        raise HTTPException(status_code=400, detail="invalid_direction")

    bridge = PrometheusStagedExitBridge(request.app.state.redis)
    if body.close_pct >= 1.0:
        await bridge.full_close(
            order_id=body.order_id,
            symbol=body.symbol,
            direction=direction,
            reason=body.reason,
        )
        return {"status": "full_close_published", "symbol": body.symbol.upper()}

    await bridge.partial_close(
        order_id=body.order_id,
        symbol=body.symbol,
        direction=direction,
        close_pct=body.close_pct,
        reason=body.reason,
    )
    return {"status": "partial_close_published", "symbol": body.symbol.upper()}
