"""Tests for the position reconciliation loop — Session 26.

14 tests covering:
  - Entry-notional vs mark-price size comparison (the critical regression)
  - Critical discrepancy halting (side mismatch, margin mode)
  - Phantom persistence logic
  - Wiring checks (correct client type, canonical halt channel, persistent key)
  - Anti-regression greps
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import msgspec
import pytest

from prometheus.execution.bitget_client import BitgetExecutionClient
from prometheus.execution.models import BitgetPosition
from prometheus.kill_switch.schemas import SystemHaltEvent
from prometheus.reconciliation.models import (
    DiscrepancyType,
    ReconciliationDiscrepancy,
)
from prometheus.reconciliation.reconciler import (
    HALT_CHANNEL,
    SIZE_TOLERANCE_PCT,
    PositionReconciler,
)


# ── Fixtures ──────────────────────────────────────────────────────────


def _make_bitget_position(
    symbol: str = "BTCUSDT",
    side: str = "long",
    total: Decimal = Decimal("0.5"),
    average_open_price: Decimal = Decimal("20000"),
    mark_price: Decimal = Decimal("20000"),
    margin_mode: str = "isolated",
) -> BitgetPosition:
    """Construct a minimal BitgetPosition for testing."""
    return BitgetPosition(
        symbol=symbol,
        product_type="USDT-FUTURES",
        margin_mode=margin_mode,     # type: ignore[arg-type]
        margin_coin="USDT",
        side=side,                   # type: ignore[arg-type]
        total=total,
        available=total,
        leverage=Decimal("10"),
        average_open_price=average_open_price,
        mark_price=mark_price,
        unrealized_pnl=Decimal("0"),
        margin_size=Decimal("100"),
        liquidation_price=None,
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_oms_position(
    asset: str = "BTC",
    position_side: str = "long",
    position_notional_usd: str = "10000",
) -> dict[str, object]:
    """Construct a minimal OMS position dict."""
    return {
        "asset": asset,
        "position_side": position_side,
        "position_notional_usd": Decimal(position_notional_usd),
    }


def _make_contract_spec(
    price_end_step: Decimal = Decimal("0"),
) -> MagicMock:
    """Build a mock contract spec with configurable price_end_step."""
    spec = MagicMock()
    spec.price_end_step = price_end_step
    return spec


def _make_reconciler(
    redis_client: Any = None,
    pg_pool: Any = None,
    bitget_client: Any = None,
) -> PositionReconciler:
    """Build a PositionReconciler with default mocks."""
    if redis_client is None:
        redis_client = AsyncMock()
    if pg_pool is None:
        pg_pool = AsyncMock()
    if bitget_client is None:
        bitget_client = MagicMock(spec=BitgetExecutionClient)
        # Default: no quantization, contract spec returns step=0
        bitget_client.get_contract_spec = AsyncMock(
            return_value=_make_contract_spec(),
        )
    return PositionReconciler(
        redis_client=redis_client,
        pg_pool=pg_pool,
        bitget_client=bitget_client,
        paper_trading=True,
    )


def _make_pg_pool_mock(conn_mock: AsyncMock) -> MagicMock:
    """Build a mock asyncpg.Pool with working ``async with pool.acquire()``."""
    @asynccontextmanager
    async def _acquire() -> AsyncIterator[AsyncMock]:
        yield conn_mock

    pg_mock = MagicMock()
    pg_mock.acquire = _acquire
    return pg_mock


# ── Test 1 — Entry-notional comparison, NOT mark-price ───────────────


class TestSizeComparisonUsesEntryNotional:
    """OMS $10k, Bitget 0.5 BTC @ avg_open=20000 → $10k entry, no divergence.

    Mark price rising to 25000 must NOT cause a flag — entry notional
    is unchanged.
    """

    @pytest.mark.asyncio
    async def test_no_divergence_at_entry(self) -> None:
        reconciler = _make_reconciler()
        oms = _make_oms_position(position_notional_usd="10000")
        # 0.5 BTC * 20000 = 10000 USD at entry → matches OMS
        bitget = _make_bitget_position(
            total=Decimal("0.5"),
            average_open_price=Decimal("20000"),
            mark_price=Decimal("20000"),
        )
        result = await reconciler._check_size("BTC", oms, bitget)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_divergence_when_mark_price_moves(self) -> None:
        reconciler = _make_reconciler()
        oms = _make_oms_position(position_notional_usd="10000")
        # mark_price rises to 25000 — but we use average_open_price
        bitget = _make_bitget_position(
            total=Decimal("0.5"),
            average_open_price=Decimal("20000"),
            mark_price=Decimal("25000"),  # would flag if used
        )
        result = await reconciler._check_size("BTC", oms, bitget)
        assert result is None


# ── Test 2 — Size divergence within tolerance → no flag ───────────────


@pytest.mark.asyncio
async def test_size_divergence_within_tolerance_no_flag() -> None:
    """2% or less divergence → no discrepancy."""
    reconciler = _make_reconciler()
    # OMS: 10000, Bitget: 0.5 * 19800 = 9900 → 1% divergence
    oms = _make_oms_position(position_notional_usd="10000")
    bitget = _make_bitget_position(
        total=Decimal("0.5"),
        average_open_price=Decimal("19800"),
    )
    result = await reconciler._check_size("BTC", oms, bitget)
    assert result is None


# ── Test 3 — Size divergence >10% → high severity ────────────────────


@pytest.mark.asyncio
async def test_size_divergence_10pct_high_severity() -> None:
    """12% divergence → 'high' severity."""
    reconciler = _make_reconciler()
    # OMS: 10000, Bitget: 0.5 * 17600 = 8800 → 12% divergence
    oms = _make_oms_position(position_notional_usd="10000")
    bitget = _make_bitget_position(
        total=Decimal("0.5"),
        average_open_price=Decimal("17600"),
    )
    result = await reconciler._check_size("BTC", oms, bitget)
    assert result is not None
    assert result.severity == "high"
    assert result.discrepancy_type == DiscrepancyType.SIZE_DIVERGENCE


# ── Test 4 — Size divergence 5% → medium severity ────────────────────


@pytest.mark.asyncio
async def test_size_divergence_5pct_medium_severity() -> None:
    """5% divergence → 'medium' severity."""
    reconciler = _make_reconciler()
    # OMS: 10000, Bitget: 0.5 * 19000 = 9500 → 5% divergence
    oms = _make_oms_position(position_notional_usd="10000")
    bitget = _make_bitget_position(
        total=Decimal("0.5"),
        average_open_price=Decimal("19000"),
    )
    result = await reconciler._check_size("BTC", oms, bitget)
    assert result is not None
    assert result.severity == "medium"


# ── Test 5 — Side mismatch halts immediately ─────────────────────────


def _make_integration_bitget_mock(
    positions: list[BitgetPosition] | None = None,
    plan_orders: list[object] | None = None,
) -> MagicMock:
    """Build a fully wired BitgetExecutionClient mock for integration tests."""
    bitget_mock = MagicMock(spec=BitgetExecutionClient)
    bitget_mock.list_open_positions = AsyncMock(
        return_value=positions or [],
    )
    bitget_mock.list_open_plan_orders = AsyncMock(
        return_value=plan_orders or [],
    )
    bitget_mock.list_open_orders = AsyncMock(return_value=[])
    bitget_mock.get_contract_spec = AsyncMock(
        return_value=_make_contract_spec(),
    )
    return bitget_mock


@pytest.mark.asyncio
async def test_side_mismatch_halts_immediately() -> None:
    """OMS long, Bitget short → halt published on first detection."""
    redis_mock = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[
        {"asset": "BTC", "position_side": "long",
         "position_notional_usd": Decimal("10000")},
    ])
    conn_mock.execute = AsyncMock()
    pg_mock = _make_pg_pool_mock(conn_mock)

    bitget_mock = _make_integration_bitget_mock(
        positions=[_make_bitget_position(side="short")],
    )

    reconciler = PositionReconciler(
        redis_client=redis_mock,
        pg_pool=pg_mock,
        bitget_client=bitget_mock,
    )
    report = await reconciler.reconcile_once()

    assert report.halt_triggered is True
    side_discs = [
        d for d in report.discrepancies
        if d.discrepancy_type == DiscrepancyType.SIDE_MISMATCH
    ]
    assert len(side_discs) >= 1
    redis_mock.publish.assert_called()


# ── Test 6 — Margin mode wrong halts immediately ─────────────────────


@pytest.mark.asyncio
async def test_margin_mode_wrong_halts_immediately() -> None:
    """Cross margin → halt published immediately."""
    redis_mock = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[
        {"asset": "BTC", "position_side": "long",
         "position_notional_usd": Decimal("10000")},
    ])
    conn_mock.execute = AsyncMock()
    pg_mock = _make_pg_pool_mock(conn_mock)

    bitget_mock = _make_integration_bitget_mock(
        positions=[_make_bitget_position(margin_mode="crossed")],
    )

    reconciler = PositionReconciler(
        redis_client=redis_mock,
        pg_pool=pg_mock,
        bitget_client=bitget_mock,
    )
    report = await reconciler.reconcile_once()

    assert report.halt_triggered is True
    margin_discs = [
        d for d in report.discrepancies
        if d.discrepancy_type == DiscrepancyType.MARGIN_MODE_WRONG
    ]
    assert len(margin_discs) >= 1


# ── Test 7 — Phantom does NOT halt on first run ──────────────────────


def test_phantom_position_does_not_halt_first_run() -> None:
    """First detection → flagged but no halt."""
    reconciler = _make_reconciler()
    should_halt = reconciler._track_phantom("BTCUSDT", True)
    assert should_halt is False


# ── Test 8 — Phantom halts after two consecutive runs ─────────────────


def test_phantom_position_halts_after_two_consecutive_runs() -> None:
    """Phantom persists across 2 runs → halt."""
    reconciler = _make_reconciler()
    reconciler._track_phantom("BTCUSDT", True)
    should_halt = reconciler._track_phantom("BTCUSDT", True)
    assert should_halt is True


# ── Test 9 — Phantom clears when OMS catches up ──────────────────────


def test_phantom_clears_when_oms_catches_up() -> None:
    """OMS commits between runs → counter resets; no halt."""
    reconciler = _make_reconciler()
    reconciler._track_phantom("BTCUSDT", True)  # first detection
    reconciler._track_phantom("BTCUSDT", False)  # OMS caught up
    should_halt = reconciler._track_phantom("BTCUSDT", True)  # appears again
    assert should_halt is False  # only 1 consecutive run


# ── Test 10 — Uses BitgetExecutionClient, not DirectClient ───────────


def test_uses_bitget_execution_client_not_direct() -> None:
    """Constructor accepts BitgetExecutionClient; rejects other types."""
    client_mock = MagicMock(spec=BitgetExecutionClient)
    client_mock.get_contract_spec = AsyncMock(
        return_value=_make_contract_spec(),
    )
    reconciler = _make_reconciler(bitget_client=client_mock)
    # Reconciler was created successfully — it accepts the correct type
    assert reconciler._bitget is client_mock

    # Type annotation on the constructor enforces BitgetExecutionClient
    import inspect
    sig = inspect.signature(PositionReconciler.__init__)
    param = sig.parameters["bitget_client"]
    assert "BitgetExecutionClient" in str(param.annotation)


# ── Test 11 — Halt published to canonical channel ─────────────────────


@pytest.mark.asyncio
async def test_halt_published_to_canonical_channel() -> None:
    """Assert publish('prometheus:system_halt', ...) called with msgspec-encoded event."""
    redis_mock = AsyncMock()
    reconciler = _make_reconciler(redis_client=redis_mock)

    await reconciler._publish_halt(
        "RECONCILER_MISMATCH",
        {"discrepancy_count": 3},
    )

    redis_mock.publish.assert_called_once()
    call_args = redis_mock.publish.call_args
    assert call_args[0][0] == "prometheus:system_halt"

    # Verify msgspec decoding roundtrip
    encoded = call_args[0][1]
    decoded = msgspec.json.decode(encoded, type=SystemHaltEvent)
    assert decoded.event_type == "HALT"
    assert decoded.reason == "RECONCILER_MISMATCH"
    assert decoded.triggered_by == "reconciler"


# ── Test 12 — Persistent halt key also set ────────────────────────────


@pytest.mark.asyncio
async def test_persistent_halt_key_set() -> None:
    """SET prometheus:trading_halted also called on halt."""
    redis_mock = AsyncMock()
    reconciler = _make_reconciler(redis_client=redis_mock)

    await reconciler._publish_halt(
        "RECONCILER_MISMATCH",
        {"test": "value"},
    )

    redis_mock.set.assert_called_once()
    set_args = redis_mock.set.call_args
    assert set_args[0][0] == "prometheus:trading_halted"


# ── Test 13 — Anti-regression: no BitgetDirectClient ──────────────────


def test_no_bitget_direct_client_import() -> None:
    """grep equivalent — reconciler.py must not reference BitgetDirectClient."""
    import pathlib
    reconciler_path = pathlib.Path(__file__).parent / "reconciler.py"
    content = reconciler_path.read_text()
    assert "BitgetDirectClient" not in content


# ── Test 14 — Anti-regression: no mark_price in notional calc ─────────


def test_no_mark_price_in_size_comparison() -> None:
    """grep equivalent — _check_size must not use mark_price for notional calc."""
    import pathlib
    import re
    reconciler_path = pathlib.Path(__file__).parent / "reconciler.py"
    content = reconciler_path.read_text()

    # Find the _check_size method body
    check_size_match = re.search(
        r"def _check_size\(.*?\n(.*?)(?=\n    def |\nclass |\Z)",
        content,
        re.DOTALL,
    )
    assert check_size_match is not None
    check_size_body = check_size_match.group(1)

    # Must NOT contain mark_price or markPrice in notional computation
    assert "mark_price" not in check_size_body
    assert "markPrice" not in check_size_body

    # MUST contain average_open_price (positive check)
    assert "average_open_price" in check_size_body


# ── Test 15 — Cooldown skips when plan orders are in-flight ───────────


@pytest.mark.asyncio
async def test_cooldown_skips_when_plan_orders_inflight() -> None:
    """Pending plan orders → reconciler returns clean skip report."""
    redis_mock = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    pg_mock = _make_pg_pool_mock(conn_mock)

    bitget_mock = MagicMock(spec=BitgetExecutionClient)
    bitget_mock.list_open_plan_orders = AsyncMock(return_value=[
        MagicMock(order_id="PLAN-123", status="not_trigger"),
    ])
    bitget_mock.list_open_orders = AsyncMock(return_value=[])

    reconciler = PositionReconciler(
        redis_client=redis_mock,
        pg_pool=pg_mock,
        bitget_client=bitget_mock,
    )
    report = await reconciler.reconcile_once()

    assert report.all_clear is True
    assert report.oms_positions_checked == 0
    assert report.halt_triggered is False


# ── Test 16 — Cooldown skips when open orders exist ───────────────────


@pytest.mark.asyncio
async def test_cooldown_skips_when_open_orders_exist() -> None:
    """Open regular orders → reconciler returns clean skip report."""
    redis_mock = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    pg_mock = _make_pg_pool_mock(conn_mock)

    bitget_mock = MagicMock(spec=BitgetExecutionClient)
    bitget_mock.list_open_plan_orders = AsyncMock(return_value=[])
    bitget_mock.list_open_orders = AsyncMock(return_value=[
        MagicMock(order_id="ORD-456", status="live"),
    ])

    reconciler = PositionReconciler(
        redis_client=redis_mock,
        pg_pool=pg_mock,
        bitget_client=bitget_mock,
    )
    report = await reconciler.reconcile_once()

    assert report.all_clear is True
    assert report.oms_positions_checked == 0


# ── Test 17 — No cooldown when no orders pending ─────────────────────


@pytest.mark.asyncio
async def test_no_cooldown_when_no_orders() -> None:
    """No in-flight orders → reconciler proceeds normally."""
    redis_mock = AsyncMock()
    conn_mock = AsyncMock()
    conn_mock.fetch = AsyncMock(return_value=[])
    conn_mock.execute = AsyncMock()
    pg_mock = _make_pg_pool_mock(conn_mock)

    bitget_mock = _make_integration_bitget_mock()

    reconciler = PositionReconciler(
        redis_client=redis_mock,
        pg_pool=pg_mock,
        bitget_client=bitget_mock,
    )
    report = await reconciler.reconcile_once()

    # Should have proceeded to check positions
    bitget_mock.list_open_positions.assert_called_once()


# ── Test 18 — _compute_divergence with quantization step ──────────────


def test_compute_divergence_quantizes_correctly() -> None:
    """Verify that sub-step rounding differences are eliminated."""
    from prometheus.reconciliation.reconciler import _compute_divergence

    # Without quantization, 10000.05 vs 10000.00 = 0.0005% divergence
    # With step=0.1, both quantize to 10000.0 → 0% divergence
    oms = Decimal("10000.05")
    bitget = Decimal("10000.00")
    step = Decimal("0.1")

    div = _compute_divergence(oms, bitget, step)
    assert div == Decimal("0")  # quantized away


# ── Test 19 — _compute_divergence zero step falls through ────────────


def test_compute_divergence_zero_step_no_quantization() -> None:
    """step=0 → raw divergence, no quantization applied."""
    from prometheus.reconciliation.reconciler import _compute_divergence

    oms = Decimal("10000")
    bitget = Decimal("9500")
    step = Decimal("0")

    div = _compute_divergence(oms, bitget, step)
    assert div == Decimal("0.05")  # 500/10000 = 5%


@pytest.mark.asyncio
async def test_pg_redis_open_asset_mismatch_halts() -> None:
    """Postgres OMS and Redis ``positions:open`` disagree → halt."""
    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(
        return_value=msgspec.json.encode(["ETH/USDT"]),
    )
    reconciler = _make_reconciler(redis_client=redis_mock)
    oms = [_make_oms_position(asset="BTC")]
    discrepancies: list[ReconciliationDiscrepancy] = []
    halt = await reconciler._compare_pg_oms_to_redis_mirror(oms, discrepancies)
    assert halt is True
    types = {d.discrepancy_type for d in discrepancies}
    assert DiscrepancyType.OMS_REDIS_DIVERGENCE in types

