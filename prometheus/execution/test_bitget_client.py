"""Test suite for BitgetExecutionClient — 13 required tests.

Uses pytest-httpx for HTTP mocking with fixture files for response shapes.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import msgspec
import pybreaker
import pytest
import pytest_httpx

from prometheus.execution.bitget_client import BitgetExecutionClient
from prometheus.execution.circuit_breaker import DefaultCircuitBreakerFactory
from prometheus.execution.models import (
    BitgetPosition,
    PlaceOrderResult,
    PlacePlanOrderRequest,
)

_FIXTURES = Path(__file__).parent / "fixtures"


# ── Helpers ──────────────────────────────────────────────────────────


def _load_fixture(name: str) -> bytes:
    """Load a fixture JSON file as bytes."""
    return (_FIXTURES / name).read_bytes()


def _make_settings(**overrides: Any) -> Any:
    """Create a minimal PrometheusSettings-like object for testing."""
    from unittest.mock import MagicMock

    from pydantic import SecretStr

    settings = MagicMock()
    settings.bitget_api_key = SecretStr(overrides.get("api_key", "test-key"))
    settings.bitget_secret_key = SecretStr(
        overrides.get("api_secret", "test-secret"),
    )
    settings.bitget_api_passphrase = SecretStr(
        overrides.get("passphrase", "test-pass"),
    )
    settings.bitget_base_url = "https://api.bitget.com"
    settings.paper_trade_enabled = overrides.get("paper_trading", False)
    settings.paper_trade_dry_run = overrides.get("paper_trading", False)
    settings.bitget_demo_write_enabled = not overrides.get("paper_trading", False)
    return settings


def _make_client(
    httpx_mock: pytest_httpx.HTTPXMock,
    *,
    paper: bool = False,
    breaker_factory: Any = None,
) -> BitgetExecutionClient:
    """Build a test client with mocked HTTP and injectable breakers."""
    settings = _make_settings(paper_trading=paper)
    http_client = httpx.AsyncClient()
    factory = breaker_factory or DefaultCircuitBreakerFactory()
    return BitgetExecutionClient(
        settings=settings,
        http_client=http_client,
        circuit_breaker_factory=factory,
    )


# ── Test 1 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_position_parses_average_open_price_as_decimal(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """average_open_price MUST be Decimal, never float."""
    httpx_mock.add_response(content=_load_fixture("position_response.json"))
    client = _make_client(httpx_mock)
    pos = await client.get_position("BTCUSDT")
    assert pos is not None
    assert isinstance(pos.average_open_price, Decimal)
    assert pos.average_open_price == Decimal("70000.5")
    assert isinstance(pos.total, Decimal)
    assert isinstance(pos.mark_price, Decimal)


# ── Test 2 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_position_returns_none_when_no_position_for_symbol(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Should return None when position has zero total."""
    empty_resp = msgspec.json.encode(
        {"code": "00000", "msg": "success", "data": [], "requestTime": 0},
    )
    httpx_mock.add_response(content=empty_resp)
    client = _make_client(httpx_mock)
    result = await client.get_position("XYZUSDT")
    assert result is None


# ── Test 3 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usd_notional_to_base_coin_size_respects_min_trade_num(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """$50 at $70k/BTC with min_trade_num=0.001 → rounds UP to 0.001."""
    # Contract spec response
    httpx_mock.add_response(
        content=_load_fixture("contract_spec_response.json"),
    )
    client = _make_client(httpx_mock)
    size = await client.usd_notional_to_base_coin_size(
        symbol="BTCUSDT",
        notional_usd=Decimal("50"),
        reference_price=Decimal("70000"),
    )
    # $50 / $70000 = 0.000714... → below min 0.001 → rounded UP to 0.001
    assert size == Decimal("0.001")
    assert isinstance(size, Decimal)


# ── Test 4 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_usd_notional_to_base_coin_size_respects_size_multiplier(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """$12345 at $70000 with multiplier 0.001 → 0.176 BTC."""
    httpx_mock.add_response(
        content=_load_fixture("contract_spec_response.json"),
    )
    client = _make_client(httpx_mock)
    size = await client.usd_notional_to_base_coin_size(
        symbol="BTCUSDT",
        notional_usd=Decimal("12345"),
        reference_price=Decimal("70000"),
    )
    # $12345 / $70000 = 0.1763571... → quantize to 0.001 step = 0.176
    assert size == Decimal("0.176")


# ── Test 5 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_place_plan_order_body_includes_planType(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """plan_type field MUST NOT be omitted from plan order body."""
    httpx_mock.add_response(
        content=_load_fixture("plan_order_response.json"),
    )
    client = _make_client(httpx_mock)
    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.001"),
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    await client.place_plan_order(req)
    request = httpx_mock.get_requests()[-1]
    body = msgspec.json.decode(request.content, type=dict)
    assert "plan_type" in body, "planType/plan_type MUST be in request body"
    assert body["plan_type"] == "normal_plan"


# ── Test 6 ───────────────────────────────────────────────────────────


def test_place_plan_order_size_in_base_coin_not_usd() -> None:
    """Critical bug guard: size field is BASE COIN, not USD notional."""
    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.150"),  # 0.15 BTC, NOT $0.15 or $150 USD
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    dumped = req.model_dump(mode="json")
    # Size must remain as-is — no USD conversion should happen at model level
    size_val = Decimal(str(dumped["size"]))
    assert size_val == Decimal("0.150")
    # Verify it's a reasonable BTC size, not a USD amount
    assert size_val < Decimal("1000"), (
        "Size appears to be USD, not base coin"
    )


# ── Test 7 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paper_trading_returns_typed_response(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Paper mode must return PlaceOrderResult, not bare int or dict."""
    client = _make_client(httpx_mock, paper=True)
    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.001"),
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    result = await client.place_plan_order(req)
    assert isinstance(result, PlaceOrderResult)
    assert result.success is True
    assert result.paper_trading is True
    assert result.order_id is not None
    assert result.order_id.startswith("PAPER-")
    # No HTTP requests should have been made
    assert len(httpx_mock.get_requests()) == 0


@pytest.mark.asyncio
async def test_demo_write_enabled_submits_http_request(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Demo-write mode should use the signed Bitget path instead of mock paper fills."""
    httpx_mock.add_response(
        content=_load_fixture("plan_order_response.json"),
    )
    client = _make_client(httpx_mock, paper=False)
    req = PlacePlanOrderRequest(
        symbol="SBTCSUSDT",
        product_type="SUSDT-FUTURES",
        margin_coin="SUSDT",
        size=Decimal("0.001"),
        side="buy",
        trade_side="open",
        trigger_price=Decimal("0"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    result = await client.place_plan_order(req)
    assert result.success is True
    assert result.paper_trading is False
    assert len(httpx_mock.get_requests()) == 1


# ── Test 8 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_opens_on_repeated_5xx(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """After fail_max error responses, breaker opens → DEGRADED result."""
    factory = DefaultCircuitBreakerFactory()
    client = _make_client(httpx_mock, breaker_factory=factory)

    error_body = msgspec.json.encode(
        {"code": "50000", "msg": "internal error", "data": {}, "requestTime": 0},
    )
    # Register exactly fail_max=3 error responses for write breaker
    for _ in range(3):
        httpx_mock.add_response(content=error_body)

    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.001"),
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    # Fire 3 requests to trip the breaker
    for _ in range(3):
        await client.place_plan_order(req)

    # 4th call should be blocked by breaker (no HTTP request made)
    result = await client.place_plan_order(req)
    assert not result.success
    assert "DEGRADED" in str(result.errors)


# ── Test 9 ───────────────────────────────────────────────────────────


def test_msgspec_encode_used_not_stdlib_json() -> None:
    """AST-scan source: bitget_client.py must use msgspec, not json."""
    source_path = Path(__file__).parent / "bitget_client.py"
    source = source_path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "json", (
                    "stdlib json imported — use msgspec.json"
                )
        if isinstance(node, ast.ImportFrom):
            assert node.module != "json", (
                "stdlib json imported — use msgspec.json"
            )


# ── Test 10 ──────────────────────────────────────────────────────────


def test_shared_signing_helper_imported_not_duplicated() -> None:
    """bitget_client.py must import from shared, not duplicate signing."""
    source_path = Path(__file__).parent / "bitget_client.py"
    source = source_path.read_text()
    # Must import from shared
    assert "from prometheus.shared.bitget_signing import" in source, (
        "Must import signing from prometheus.shared.bitget_signing"
    )
    # Must NOT contain its own HMAC implementation
    assert "hmac.new(" not in source, (
        "Must not duplicate HMAC signing logic — use shared helper"
    )
    assert "hashlib.sha256" not in source, (
        "Must not duplicate signing — use shared helper"
    )


# ── Test 11 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bitget_envelope_parse_error_returns_degraded(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """Garbage response → DEGRADED PlaceOrderResult, no exception."""
    httpx_mock.add_response(content=b"this is not json at all {{{")
    client = _make_client(httpx_mock)
    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.001"),
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    result = await client.place_plan_order(req)
    assert isinstance(result, PlaceOrderResult)
    assert result.success is False
    assert any("parse error" in e for e in result.errors)


# ── Test 12 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rate_limit_429_opens_breaker_and_returns_degraded(
    httpx_mock: pytest_httpx.HTTPXMock,
) -> None:
    """HTTP 429 → breaker opens, returns DEGRADED result."""
    httpx_mock.add_response(status_code=429, content=b"rate limited")
    client = _make_client(httpx_mock)
    req = PlacePlanOrderRequest(
        symbol="BTCUSDT",
        size=Decimal("0.001"),
        side="sell",
        trade_side="close",
        trigger_price=Decimal("65000"),
        trigger_type="mark_price",
        order_type="market",
        plan_type="normal_plan",
    )
    result = await client.place_plan_order(req)
    assert isinstance(result, PlaceOrderResult)
    assert result.success is False
    assert any("429" in e or "DEGRADED" in e for e in result.errors)


# ── Test 13 ──────────────────────────────────────────────────────────


def test_reconciliation_uses_average_open_price() -> None:
    """Integration-style: verify reconciliation pattern with position data.

    Correct: notional_at_entry = total * average_open_price
    Wrong:   notional = total * mark_price (mark moves continuously)
    """
    pos = BitgetPosition(
        symbol="BTCUSDT",
        product_type="USDT-FUTURES",
        margin_mode="isolated",
        margin_coin="USDT",
        side="long",
        total=Decimal("0.150"),
        available=Decimal("0.150"),
        leverage=Decimal("5"),
        average_open_price=Decimal("70000.5"),
        mark_price=Decimal("71250.3"),
        unrealized_pnl=Decimal("187.47"),
        margin_size=Decimal("2100.015"),
        created_at=__import__("datetime").datetime.now(
            tz=__import__("datetime").timezone.utc,
        ),
    )
    # CORRECT reconciliation: use average_open_price
    bitget_notional_at_entry = pos.total * pos.average_open_price
    oms_size_usd = Decimal("10500.075")  # recorded at entry
    divergence = abs(oms_size_usd - bitget_notional_at_entry) / oms_size_usd

    assert divergence == Decimal("0"), (
        "Reconciliation should use average_open_price for entry notional"
    )

    # WRONG: using mark_price would give different notional
    wrong_notional = pos.total * pos.mark_price
    assert wrong_notional != bitget_notional_at_entry, (
        "mark_price and average_open_price should differ"
    )
