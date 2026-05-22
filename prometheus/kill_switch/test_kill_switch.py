"""Tests for the emergency kill switch module.

Covers:
    - Core halt/resume with Redis key and channel verification
    - HMAC constant-time panic key verification
    - Audit log disk writes via asyncio.to_thread
    - Retry budget escalation and exhaustion
    - All trigger functions
    - HTTP panic endpoint rate limiting and authentication
    - Import hygiene (no aiofiles)
"""

import asyncio
import hmac
import os
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from prometheus.kill_switch.core import KillSwitch
from prometheus.kill_switch.http_endpoint import (
    _rate_limits,
    create_halt_route,
)
from prometheus.kill_switch.retry_budget import RetryBudget
from prometheus.kill_switch.schemas import SystemHaltEvent
from prometheus.kill_switch.triggers import (
    api_failure_storm_trigger,
    liquidation_proximity_trigger,
    position_limit_trigger,
    spike_detector_trigger,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture()
def fake_redis() -> AsyncMock:
    """Minimal async Redis mock."""
    r = AsyncMock()
    r.set = AsyncMock()
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock()
    r.publish = AsyncMock()
    r.exists = AsyncMock(return_value=0)
    return r


@pytest.fixture()
def tmp_audit_path(tmp_path: Path) -> Path:
    """Temporary audit log file path."""
    return tmp_path / "audit.log"


@pytest.fixture()
def kill_switch(fake_redis: AsyncMock, tmp_audit_path: Path) -> KillSwitch:
    """KillSwitch instance backed by mock Redis and tmp audit path."""
    return KillSwitch(redis_client=fake_redis, audit_log_path=tmp_audit_path)


# ─── Task 2: Core Tests ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_halt_sets_redis_key_and_publishes_channel(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """Halt must SET the halt key AND PUBLISH to the canonical channel."""
    await kill_switch.halt(
        reason="MANUAL_PANIC_KEY",
        triggered_by="test",
        details={"source": "unit_test"},
    )
    fake_redis.set.assert_called_once()
    call_args = fake_redis.set.call_args
    assert call_args[0][0] == "prometheus:trading_halted"

    fake_redis.publish.assert_called_once()
    pub_args = fake_redis.publish.call_args
    assert pub_args[0][0] == "prometheus:system_halt"

    # Verify published payload is valid SystemHaltEvent
    payload = pub_args[0][1]
    event = msgspec.json.decode(payload, type=SystemHaltEvent)
    assert event.event_type == "HALT"
    assert event.reason == "MANUAL_PANIC_KEY"


@pytest.mark.asyncio
async def test_resume_requires_fresh_panic_key(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """Resume must be denied when an invalid panic key is provided."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "correct-key"}):
        result = await kill_switch.resume("wrong-key")
    assert result is False
    fake_redis.delete.assert_not_called()


@pytest.mark.asyncio
async def test_resume_succeeds_with_valid_key(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """Resume must succeed and clear halt key when valid key provided."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "correct-key"}):
        result = await kill_switch.resume("correct-key")
    assert result is True
    fake_redis.delete.assert_called_once_with("prometheus:trading_halted")
    # Resume trigger key should be set with TTL
    resume_call = fake_redis.set.call_args
    assert resume_call[0][0] == "prometheus:resume_trigger"
    assert resume_call[1]["ex"] == 60


def test_panic_key_verification_uses_constant_time(
    kill_switch: KillSwitch,
) -> None:
    """Verify ``hmac.compare_digest`` is called — NEVER ``==``."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "test-key"}):
        with patch("prometheus.kill_switch.core.hmac.compare_digest", return_value=True) as mock_cmp:
            result = kill_switch.verify_panic_key("test-key")
            mock_cmp.assert_called_once()
            assert result is True

    # Grep-based: ensure no string equality for key comparison
    source = Path(__file__).parent / "core.py"
    content = source.read_text()
    assert "expected ==" not in content
    assert "provided ==" not in content
    assert "== expected" not in content
    assert "== provided" not in content


@pytest.mark.asyncio
async def test_audit_log_written_on_halt(
    kill_switch: KillSwitch,
    tmp_audit_path: Path,
) -> None:
    """Halt must write audit bytes to the log file."""
    await kill_switch.halt(
        reason="SPIKE_DETECTOR",
        triggered_by="test",
        details={"pnl": "-0.05"},
    )
    assert tmp_audit_path.exists()
    raw = tmp_audit_path.read_bytes()
    assert len(raw) > 0
    # Must be valid JSON ending with newline
    assert raw.endswith(b"\n")
    event = msgspec.json.decode(raw.strip(), type=SystemHaltEvent)
    assert event.event_type == "HALT"


@pytest.mark.asyncio
async def test_is_halted_reads_redis_key(
    kill_switch: KillSwitch,
    fake_redis: AsyncMock,
) -> None:
    """``is_halted()`` must reflect the Redis halt key state."""
    fake_redis.exists.return_value = 0
    assert await kill_switch.is_halted() is False

    fake_redis.exists.return_value = 1
    assert await kill_switch.is_halted() is True


@pytest.mark.asyncio
async def test_audit_write_uses_asyncio_to_thread(
    kill_switch: KillSwitch,
) -> None:
    """Audit writes must go through ``asyncio.to_thread`` — no aiofiles."""
    with patch("prometheus.kill_switch.core.asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
        await kill_switch._append_audit(b"test payload\n")
        mock_thread.assert_called_once()


# ─── Task 3: Retry Budget Tests ─────────────────────────────────────────────


def test_retry_budget_escalates_at_60s() -> None:
    """Budget should escalate at 60 seconds."""
    budget = RetryBudget()
    assert budget.should_escalate() is False

    # Simulate 60s elapsed
    budget._elapsed = 60.0
    assert budget.should_escalate() is True
    assert budget.budget_exhausted() is False


def test_retry_budget_exhausted_at_120s() -> None:
    """Budget should be exhausted at 120 seconds."""
    budget = RetryBudget()
    assert budget.budget_exhausted() is False

    # Simulate 120s elapsed
    budget._elapsed = 120.0
    assert budget.budget_exhausted() is True


def test_retry_budget_tick_advances_clock() -> None:
    """``tick()`` must advance the internal elapsed counter."""
    budget = RetryBudget()
    initial = budget._elapsed
    time.sleep(0.01)
    elapsed = budget.tick()
    assert elapsed > initial


# ─── Task 4: Trigger Tests ──────────────────────────────────────────────────


def test_spike_detector_trigger() -> None:
    """Known P&L series must produce correct halt decisions."""
    # Safe P&L — no halt
    safe_pnl = [Decimal("0.01"), Decimal("-0.01"), Decimal("0.005")]
    should_halt, reason, details = spike_detector_trigger(safe_pnl)
    assert should_halt is False
    assert reason == "SPIKE_DETECTOR"

    # Dangerous P&L — halt
    bad_pnl = [Decimal("-0.02"), Decimal("-0.015"), Decimal("-0.005")]
    should_halt, reason, details = spike_detector_trigger(bad_pnl)
    assert should_halt is True
    assert reason == "SPIKE_DETECTOR"
    assert "rolling_pnl" in details

    # Empty history — no halt
    should_halt, _, _ = spike_detector_trigger([])
    assert should_halt is False


def test_position_limit_trigger() -> None:
    """Position limit breach must trigger halt."""
    positions: list[dict[str, object]] = [
        {"asset": "BTC"}, {"asset": "ETH"}, {"asset": "SOL"},
    ]
    should_halt, reason, _ = position_limit_trigger(positions, max_positions=2)
    assert should_halt is True
    assert reason == "POSITION_LIMIT_BREACH"

    # Within limit — no halt
    should_halt, _, _ = position_limit_trigger(positions, max_positions=5)
    assert should_halt is False


def test_api_failure_storm_trigger() -> None:
    """API failure storm trigger must fire when budget is exhausted."""
    budget = RetryBudget()
    should_halt, _, _ = api_failure_storm_trigger(budget)
    assert should_halt is False

    budget._elapsed = 120.0
    should_halt, reason, details = api_failure_storm_trigger(budget)
    assert should_halt is True
    assert reason == "API_FAILURE_STORM"


def test_liquidation_proximity_trigger() -> None:
    """Liquidation proximity must trigger when ratio >= threshold."""
    # At risk: price 0.90 / liq 1.00 = 0.90 >= 0.85
    positions = [
        {"current_price": Decimal("0.90"), "liquidation_price": Decimal("1.00")},
    ]
    should_halt, reason, _ = liquidation_proximity_trigger(positions)
    assert should_halt is True
    assert reason == "LIQUIDATION_PROXIMITY"

    # Safe: price 0.50 / liq 1.00 = 0.50 < 0.85
    safe_positions = [
        {"current_price": Decimal("0.50"), "liquidation_price": Decimal("1.00")},
    ]
    should_halt, _, _ = liquidation_proximity_trigger(safe_positions)
    assert should_halt is False


# ─── Task 5: HTTP Endpoint Tests ────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_rate_limits() -> None:
    """Clear the in-memory rate limit state between tests."""
    _rate_limits.clear()


@pytest.fixture()
async def test_app(kill_switch: KillSwitch):  # type: ignore[no-untyped-def]
    """FastAPI AsyncClient with the halt route mounted."""
    from fastapi import FastAPI
    import httpx

    app = FastAPI()
    halt_router = create_halt_route(kill_switch)
    app.include_router(halt_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        yield client


@pytest.mark.asyncio
async def test_http_panic_endpoint_rejects_bad_key(
    test_app: MagicMock,
) -> None:
    """Bad panic key must return HTTP 403."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "real-key"}):
        resp = await test_app.post(
            "/kill-switch/halt",
            headers={"X-Panic-Key": "wrong-key"},
        )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_http_panic_endpoint_rate_limited(
    test_app: MagicMock,
) -> None:
    """4th attempt within 60 seconds must return HTTP 429."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "real-key"}):
        for _ in range(3):
            await test_app.post(
                "/kill-switch/halt",
                headers={"X-Panic-Key": "wrong-key"},
            )
        resp = await test_app.post(
            "/kill-switch/halt",
            headers={"X-Panic-Key": "wrong-key"},
        )
    assert resp.status_code == 429


@pytest.mark.asyncio
async def test_http_panic_endpoint_succeeds_with_valid_key(
    test_app: MagicMock,
) -> None:
    """Valid panic key must return HTTP 200 with halt confirmation."""
    with patch.dict(os.environ, {"POLARIS_PANIC_KEY": "real-key"}):
        resp = await test_app.post(
            "/kill-switch/halt",
            headers={"X-Panic-Key": "real-key"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "halted"


# ─── Hygiene Tests ───────────────────────────────────────────────────────────


def test_no_aiofiles_import() -> None:
    """No source file in the kill switch module may import ``aiofiles``."""
    module_dir = Path(__file__).parent
    for py_file in module_dir.glob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        content = py_file.read_text()
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            assert "import aiofiles" not in stripped, (
                f"aiofiles import in {py_file.name} — use asyncio.to_thread"
            )


def test_no_atlas_imports() -> None:
    """Kill switch must have zero ATLAS imports — standalone invariant."""
    module_dir = Path(__file__).parent
    for py_file in module_dir.glob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        content = py_file.read_text()
        assert "from atlas" not in content, (
            f"ATLAS import found in {py_file.name} — standalone violation"
        )
        assert "import atlas" not in content, (
            f"ATLAS import found in {py_file.name} — standalone violation"
        )
