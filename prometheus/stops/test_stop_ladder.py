"""Test suite for the tiered stop-loss ladder (Session 25).

Minimum 13 tests covering:
 1. Tier size fractions sum to 1.0
 2. ATR tighter → ATR used
 3. Fixed tighter → fixed used
 4. No ATR in Redis → fixed, no crash
 5. Paper ON → paper_placed, Bitget not called
 6. Paper OFF → place_plan_order with correct fields
 7. size_base_coin populated and persisted
 8. cancel_tier → correct args
 9. Failed placement → status=failed, error logged
10. Grep: no place-tpsl-order
11. Grep: no str(tier.size_usd) as order body
12. Grep: no _sign_headers on StopLadderPlacer
13. ATR key format is canonical
"""

from __future__ import annotations

import subprocess
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from prometheus.stops.calculator import StopLadderCalculator
from prometheus.stops.models import StopTier, TieredStopLoss
from prometheus.stops.placer import StopLadderPlacer

# ── Shared fixtures ───────────────────────────────────────────────────

STOPS_DIR = str(Path(__file__).resolve().parent)


def _mock_redis(atr_val: str | None = None) -> AsyncMock:
    """Build a mock Redis client with optional ATR data."""
    redis = AsyncMock()
    if atr_val is not None:
        import msgspec
        redis.get = AsyncMock(
            return_value=msgspec.json.encode({"atr": atr_val}),
        )
    else:
        redis.get = AsyncMock(return_value=None)
    return redis


def _make_ladder(
    atr_used: bool = False,
    atr_value: Decimal | None = None,
) -> TieredStopLoss:
    """Build a test ladder for placer tests."""
    return TieredStopLoss(
        trade_id="test-trade-001",
        asset="BTC",
        position_side="long",
        entry_price=Decimal("50000"),
        position_notional_usd=Decimal("10000"),
        leverage=Decimal("10"),
        tier_1=StopTier(
            tier=1, price=Decimal("47500"),
            size_pct=Decimal("0.33"),
            size_usd=Decimal("3300"),
        ),
        tier_2=StopTier(
            tier=2, price=Decimal("45000"),
            size_pct=Decimal("0.33"),
            size_usd=Decimal("3300"),
        ),
        tier_3=StopTier(
            tier=3, price=Decimal("42500"),
            size_pct=Decimal("0.34"),
            size_usd=Decimal("3400"),
        ),
        atr_value=atr_value,
        used_atr_pricing=atr_used,
    )


def _mock_bitget() -> AsyncMock:
    """Build a mock BitgetExecutionClient."""
    from prometheus.execution.models import PlaceOrderResult

    client = AsyncMock()
    client.usd_notional_to_base_coin_size = AsyncMock(
        return_value=Decimal("0.066"),
    )
    client.place_plan_order = AsyncMock(
        return_value=PlaceOrderResult(
            success=True,
            order_id="live-order-123",
        ),
    )
    client.cancel_plan_order = AsyncMock(return_value=True)
    return client


def _mock_pg() -> AsyncMock:
    """Build a mock asyncpg Pool."""
    pool = AsyncMock()
    pool.execute = AsyncMock(return_value=None)
    return pool


# ── Test 1: Tier fractions sum to 1.0 ─────────────────────────────────


def test_tier_size_fractions_sum_to_one() -> None:
    total = sum(StopLadderCalculator.TIER_SIZES)
    assert total == Decimal("1.00")


# ── Test 2: ATR tighter → ATR prices used ────────────────────────────


@pytest.mark.asyncio
async def test_atr_tighter_than_fixed_uses_atr() -> None:
    """ATR offset < fixed offset → ATR price used, used_atr_pricing=True."""
    # ATR=500 → tier1 offset=500, fixed=0.05*50000=2500 → ATR is tighter
    redis = _mock_redis(atr_val="500")
    calc = StopLadderCalculator(redis)
    ladder = await calc.calculate(
        trade_id="t1", asset="BTC", side="long",
        entry_price=Decimal("50000"),
        position_notional_usd=Decimal("10000"),
        leverage=Decimal("10"),
    )
    assert ladder.used_atr_pricing is True
    assert ladder.atr_value == Decimal("500")
    # tier1: 50000 - 500 = 49500 (tighter than 50000 - 2500 = 47500)
    assert ladder.tier_1.price == Decimal("49500")


# ── Test 3: Fixed tighter → fixed prices used ────────────────────────


@pytest.mark.asyncio
async def test_fixed_tighter_than_atr_uses_fixed() -> None:
    """ATR offset > fixed offset → fixed price used."""
    # ATR=5000 → tier1 offset=5000, fixed=0.05*50000=2500 → fixed tighter
    redis = _mock_redis(atr_val="5000")
    calc = StopLadderCalculator(redis)
    ladder = await calc.calculate(
        trade_id="t2", asset="BTC", side="long",
        entry_price=Decimal("50000"),
        position_notional_usd=Decimal("10000"),
        leverage=Decimal("10"),
    )
    assert ladder.used_atr_pricing is True  # ATR was fetched
    # tier1: min(5000, 2500)=2500 → price=47500
    assert ladder.tier_1.price == Decimal("47500")


# ── Test 4: No ATR in Redis → fixed, no crash ────────────────────────


@pytest.mark.asyncio
async def test_no_atr_in_redis_uses_fixed_no_crash() -> None:
    redis = _mock_redis(atr_val=None)
    calc = StopLadderCalculator(redis)
    ladder = await calc.calculate(
        trade_id="t3", asset="PEPE", side="long",
        entry_price=Decimal("0.000010"),
        position_notional_usd=Decimal("1000"),
        leverage=Decimal("5"),
    )
    assert ladder.used_atr_pricing is False
    assert ladder.atr_value is None
    # Fixed: 0.000010 * 0.05 = 0.0000005 → price = 0.0000095
    assert ladder.tier_1.price == Decimal("0.0000095")


# ── Test 5: Paper ON → paper_placed, Bitget never called ─────────────


@pytest.mark.asyncio
async def test_paper_trading_on_never_calls_bitget() -> None:
    bitget = _mock_bitget()
    pg = _mock_pg()
    redis = _mock_redis()
    placer = StopLadderPlacer(
        redis_client=redis, pg_pool=pg,
        bitget_client=bitget, paper_trading=True,
    )
    ladder = _make_ladder()
    result = await placer.place_all(ladder)

    assert result.tier_1.status == "paper_placed"
    assert result.tier_2.status == "paper_placed"
    assert result.tier_3.status == "paper_placed"
    assert result.all_placed is True
    assert result.tier_1.bitget_order_id is not None
    assert result.tier_1.bitget_order_id.startswith("paper-")

    bitget.place_plan_order.assert_not_called()
    bitget.usd_notional_to_base_coin_size.assert_not_called()


# ── Test 6: Paper OFF → verify place_plan_order args ──────────────────


@pytest.mark.asyncio
async def test_live_placement_calls_bitget_correctly() -> None:
    bitget = _mock_bitget()
    pg = _mock_pg()
    redis = _mock_redis()
    placer = StopLadderPlacer(
        redis_client=redis, pg_pool=pg,
        bitget_client=bitget, paper_trading=False,
    )
    ladder = _make_ladder()
    result = await placer.place_all(ladder)

    assert result.all_placed is True
    assert bitget.place_plan_order.call_count == 3

    for call in bitget.place_plan_order.call_args_list:
        req = call.args[0]
        assert req.plan_type == "normal_plan"
        assert req.trade_side == "close"
        assert req.size == Decimal("0.066")  # from mock


# ── Test 7: size_base_coin populated and persisted ────────────────────


@pytest.mark.asyncio
async def test_size_base_coin_populated_and_persisted() -> None:
    bitget = _mock_bitget()
    pg = _mock_pg()
    redis = _mock_redis()
    placer = StopLadderPlacer(
        redis_client=redis, pg_pool=pg,
        bitget_client=bitget, paper_trading=False,
    )
    ladder = _make_ladder()
    result = await placer.place_all(ladder)

    # size_base_coin set on the returned tier model
    assert result.tier_1.size_base_coin == Decimal("0.066")
    assert result.tier_2.size_base_coin == Decimal("0.066")
    assert result.tier_3.size_base_coin == Decimal("0.066")

    # _persist_tier was called 3 times
    assert pg.execute.call_count == 3
    # Check that the persisted value is the base coin, not USD
    first_persist = pg.execute.call_args_list[0]
    args = first_persist.args
    # args[6] is size_base_coin in the SQL params
    assert args[6] == Decimal("0.066")


# ── Test 8: cancel_tier calls bitget correctly ────────────────────────


@pytest.mark.asyncio
async def test_cancel_tier_calls_bitget() -> None:
    bitget = _mock_bitget()
    pg = _mock_pg()
    redis = _mock_redis()
    placer = StopLadderPlacer(
        redis_client=redis, pg_pool=pg,
        bitget_client=bitget, paper_trading=False,
    )
    ok = await placer.cancel_tier("order-abc-123")
    assert ok is True
    bitget.cancel_plan_order.assert_called_once_with(
        order_id="order-abc-123",
        product_type="USDT-FUTURES",
    )


# ── Test 9: Failed placement → status='failed' ───────────────────────


@pytest.mark.asyncio
async def test_failed_placement_sets_failed_status() -> None:
    from prometheus.execution.models import PlaceOrderResult

    bitget = _mock_bitget()
    bitget.place_plan_order = AsyncMock(
        return_value=PlaceOrderResult(
            success=False,
            errors=["Bitget error 40001: insufficient margin"],
        ),
    )
    pg = _mock_pg()
    redis = _mock_redis()
    placer = StopLadderPlacer(
        redis_client=redis, pg_pool=pg,
        bitget_client=bitget, paper_trading=False,
    )
    ladder = _make_ladder()
    result = await placer.place_all(ladder)

    assert result.tier_1.status == "failed"
    assert result.tier_2.status == "failed"
    assert result.tier_3.status == "failed"
    assert result.all_placed is False


# ── Test 10: Grep — no "place-tpsl-order" in session files ────────────


def test_grep_no_place_tpsl_order() -> None:
    result = subprocess.run(
        ["grep", "-rn", "place-tpsl-order", STOPS_DIR,
         "--include=*.py"],
        capture_output=True, text=True,
    )
    # Filter out this test file's own references
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if "test_stop_ladder.py" not in ln
    ]
    assert len(lines) == 0, f"Found place-tpsl-order: {lines}"


# ── Test 11: Grep — no str(tier.size_usd) as order body ──────────────


def test_grep_no_usd_as_size_literal() -> None:
    result = subprocess.run(
        ["grep", "-rEn",
         r'"size":\s*str\([^)]*usd\)|size=str\([^)]*usd\)',
         STOPS_DIR, "--include=*.py"],
        capture_output=True, text=True,
    )
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if "test_stop_ladder.py" not in ln
    ]
    assert len(lines) == 0, f"Found USD-as-size: {lines}"


# ── Test 12: Grep — no _sign_headers on StopLadderPlacer ─────────────


def test_grep_no_sign_headers_on_placer() -> None:
    result = subprocess.run(
        ["grep", "-rn", "_sign_headers", STOPS_DIR,
         "--include=*.py"],
        capture_output=True, text=True,
    )
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if "test_stop_ladder.py" not in ln
    ]
    assert len(lines) == 0, f"Found _sign_headers: {lines}"


# ── Test 13: ATR key format is canonical ──────────────────────────────


def test_atr_key_format_is_canonical() -> None:
    calc = StopLadderCalculator.__new__(StopLadderCalculator)
    key = calc.ATR_KEY_FMT.format(asset="BTC", timeframe="1h")
    assert key == "atlas:atr:BTC:1h"


# ── Test 14: Short side stop prices go UP ─────────────────────────────


@pytest.mark.asyncio
async def test_short_side_stop_prices_above_entry() -> None:
    redis = _mock_redis(atr_val=None)
    calc = StopLadderCalculator(redis)
    ladder = await calc.calculate(
        trade_id="t-short", asset="ETH", side="short",
        entry_price=Decimal("3000"),
        position_notional_usd=Decimal("6000"),
        leverage=Decimal("5"),
    )
    # For shorts, stop prices should be ABOVE entry
    assert ladder.tier_1.price > ladder.entry_price
    assert ladder.tier_2.price > ladder.tier_1.price
    assert ladder.tier_3.price > ladder.tier_2.price


# ── Test 15: No banned libraries ──────────────────────────────────────


def test_grep_no_banned_libraries() -> None:
    result = subprocess.run(
        ["grep", "-rEn",
         r"aioredis|^import json\b|json\.loads|json\.dumps|CoinGlass",
         STOPS_DIR, "--include=*.py"],
        capture_output=True, text=True,
    )
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if "test_stop_ladder.py" not in ln
    ]
    assert len(lines) == 0, f"Banned library found: {lines}"


# ── Test 16: No loguru kwargs ─────────────────────────────────────────


def test_grep_no_loguru_kwargs() -> None:
    result = subprocess.run(
        ["grep", "-rEn",
         r'logger\.(info|warning|error|debug|critical|exception)\([^)]*=[^)]*\)',
         STOPS_DIR, "--include=*.py"],
        capture_output=True, text=True,
    )
    lines = [
        ln for ln in result.stdout.strip().splitlines()
        if "test_stop_ladder.py" not in ln
    ]
    assert len(lines) == 0, f"Loguru kwargs found: {lines}"


# ── Test 17: plan_type present in placer.py ───────────────────────────


def test_grep_plan_type_present_in_placer() -> None:
    placer_path = Path(STOPS_DIR) / "placer.py"
    result = subprocess.run(
        ["grep", "-n", "plan_type", str(placer_path)],
        capture_output=True, text=True,
    )
    assert result.stdout.strip(), "plan_type not found in placer.py"
