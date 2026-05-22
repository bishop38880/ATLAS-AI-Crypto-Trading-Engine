"""Tests for CRA Adjusted Cost Base (ACB) calculator — Session 27 Phase 0.

Session 26 dependency — tests for the ``prometheus/tax/`` module.

Covers:
    1. Weighted average ACB after multiple buys
    2. Disposition gain accuracy
    3. Disposition loss accuracy
    4. Exchange rate CAD/USD conversion
    5. T2125 summary aggregation
    6. 50% capital gains inclusion rate
    7. Net loss → zero taxable income
    8. Multiple assets tracked independently
    9. All Decimal — no float contamination
   10. Anti-regression: no banned libraries

Hermetic: uses ``AsyncMock`` for all external boundaries.
No live API or database calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from prometheus.tax.acb import AcbCalculator
from prometheus.tax.models import AcbLot, Disposition, T2125Summary


# ── Test 1 — Weighted average ACB after multiple buys ─────────────────


def test_weighted_average_acb_multiple_buys() -> None:
    """Two buys at different prices → weighted average ACB per unit.

    Buy 1: 2 BTC @ $40k USD, rate 1.35 → $108,000 CAD
    Buy 2: 1 BTC @ $50k USD, rate 1.40 → $70,000 CAD
    Total: 3 BTC, $178,000 CAD → ACB/unit = $59,333.333...
    """
    calc = AcbCalculator(asset="BTC")
    calc.add_acquisition(
        quantity=Decimal("2"),
        cost_usd=Decimal("80000"),   # 2 * 40000
        exchange_rate=Decimal("1.35"),
    )
    calc.add_acquisition(
        quantity=Decimal("1"),
        cost_usd=Decimal("50000"),
        exchange_rate=Decimal("1.40"),
    )
    assert calc.total_quantity == Decimal("3")
    # (80000*1.35 + 50000*1.40) / 3 = (108000 + 70000) / 3 = 59333.33...
    expected_acb = (Decimal("108000") + Decimal("70000")) / Decimal("3")
    assert calc.acb_per_unit == expected_acb


# ── Test 2 — Disposition gain accuracy ────────────────────────────────


def test_disposition_gain_accuracy() -> None:
    """Sell above ACB → positive capital gain in CAD."""
    calc = AcbCalculator(asset="ETH")
    calc.add_acquisition(
        quantity=Decimal("10"),
        cost_usd=Decimal("20000"),  # $2000/ETH
        exchange_rate=Decimal("1.30"),
    )
    # ACB/unit = 20000 * 1.30 / 10 = 2600 CAD
    disp = calc.record_disposition(
        quantity=Decimal("5"),
        proceeds_usd=Decimal("15000"),  # $3000/ETH
        exchange_rate=Decimal("1.35"),
    )
    # proceeds_cad = 15000 * 1.35 = 20250
    # total_acb = 2600 * 5 = 13000
    # gain = 20250 - 13000 = 7250
    assert disp.proceeds_cad == Decimal("20250.00")
    assert disp.total_acb_cad == Decimal("13000.00")
    assert disp.gain_loss_cad == Decimal("7250.00")
    assert disp.gain_loss_cad > 0


# ── Test 3 — Disposition loss accuracy ────────────────────────────────


def test_disposition_loss_accuracy() -> None:
    """Sell below ACB → negative capital loss in CAD."""
    calc = AcbCalculator(asset="SOL")
    calc.add_acquisition(
        quantity=Decimal("100"),
        cost_usd=Decimal("15000"),   # $150/SOL
        exchange_rate=Decimal("1.30"),
    )
    # ACB/unit = 15000 * 1.30 / 100 = 195 CAD
    disp = calc.record_disposition(
        quantity=Decimal("50"),
        proceeds_usd=Decimal("5000"),   # $100/SOL
        exchange_rate=Decimal("1.30"),
    )
    # proceeds_cad = 5000 * 1.30 = 6500
    # total_acb = 195 * 50 = 9750
    # loss = 6500 - 9750 = -3250
    assert disp.gain_loss_cad == Decimal("-3250.00")
    assert disp.gain_loss_cad < 0


# ── Test 4 — Exchange rate conversion ─────────────────────────────────


def test_exchange_rate_conversion() -> None:
    """USD amounts must be converted to CAD at trade-time rate."""
    calc = AcbCalculator(asset="BTC")
    lot = calc.add_acquisition(
        quantity=Decimal("1"),
        cost_usd=Decimal("50000"),
        exchange_rate=Decimal("1.25"),
    )
    assert lot.cost_cad == Decimal("62500.00")  # 50000 * 1.25
    assert lot.exchange_rate == Decimal("1.25")

    disp = calc.record_disposition(
        quantity=Decimal("1"),
        proceeds_usd=Decimal("60000"),
        exchange_rate=Decimal("1.30"),
    )
    assert disp.proceeds_cad == Decimal("78000.00")  # 60000 * 1.30


# ── Test 5 — T2125 summary aggregation ───────────────────────────────


def test_t2125_summary_aggregation() -> None:
    """T2125 summary must aggregate all dispositions for the tax year."""
    calc = AcbCalculator(asset="BTC")
    calc.add_acquisition(
        quantity=Decimal("5"),
        cost_usd=Decimal("250000"),
        exchange_rate=Decimal("1.30"),
    )
    # Two dispositions in 2025
    calc.record_disposition(
        quantity=Decimal("2"),
        proceeds_usd=Decimal("120000"),
        exchange_rate=Decimal("1.35"),
        disposed_at=datetime(2025, 6, 15, tzinfo=timezone.utc),
    )
    calc.record_disposition(
        quantity=Decimal("1"),
        proceeds_usd=Decimal("55000"),
        exchange_rate=Decimal("1.32"),
        disposed_at=datetime(2025, 11, 20, tzinfo=timezone.utc),
    )

    summary = calc.t2125_summary(tax_year=2025)
    assert isinstance(summary, T2125Summary)
    assert summary.tax_year == 2025
    assert summary.total_dispositions == 2
    assert summary.gross_proceeds_cad > 0
    assert summary.net_gain_loss_cad > 0


# ── Test 6 — 50% capital gains inclusion rate ─────────────────────────


def test_fifty_percent_inclusion_rate() -> None:
    """Taxable income = 50% of net capital gains (CRA rule)."""
    calc = AcbCalculator(asset="BTC")
    calc.add_acquisition(
        quantity=Decimal("1"),
        cost_usd=Decimal("10000"),
        exchange_rate=Decimal("1.00"),  # 1:1 for clean math
    )
    calc.record_disposition(
        quantity=Decimal("1"),
        proceeds_usd=Decimal("20000"),
        exchange_rate=Decimal("1.00"),
        disposed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )
    summary = calc.t2125_summary(2025)
    # gain = 20000 - 10000 = 10000
    # taxable = 10000 * 0.50 = 5000
    assert summary.net_gain_loss_cad == Decimal("10000")
    assert summary.taxable_income_cad == Decimal("5000.00")


# ── Test 7 — Net loss → zero taxable income ──────────────────────────


def test_net_loss_zero_taxable_income() -> None:
    """Net capital loss → taxable_income_cad = 0 (not negative)."""
    calc = AcbCalculator(asset="ETH")
    calc.add_acquisition(
        quantity=Decimal("10"),
        cost_usd=Decimal("30000"),
        exchange_rate=Decimal("1.00"),
    )
    calc.record_disposition(
        quantity=Decimal("10"),
        proceeds_usd=Decimal("20000"),
        exchange_rate=Decimal("1.00"),
        disposed_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
    )
    summary = calc.t2125_summary(2025)
    assert summary.net_gain_loss_cad == Decimal("-10000")
    assert summary.taxable_income_cad == Decimal("0")


# ── Test 8 — Multiple assets tracked independently ────────────────────


def test_multiple_assets_independent() -> None:
    """Each AcbCalculator instance tracks one asset independently."""
    btc_calc = AcbCalculator(asset="BTC")
    eth_calc = AcbCalculator(asset="ETH")

    btc_calc.add_acquisition(
        quantity=Decimal("1"),
        cost_usd=Decimal("50000"),
        exchange_rate=Decimal("1.30"),
    )
    eth_calc.add_acquisition(
        quantity=Decimal("10"),
        cost_usd=Decimal("30000"),
        exchange_rate=Decimal("1.30"),
    )

    assert btc_calc.total_quantity == Decimal("1")
    assert eth_calc.total_quantity == Decimal("10")
    # BTC ACB = 50000 * 1.30 / 1 = 65000
    assert btc_calc.acb_per_unit == Decimal("65000.00")
    # ETH ACB = 30000 * 1.30 / 10 = 3900
    assert eth_calc.acb_per_unit == Decimal("3900.00")


# ── Test 9 — All Decimal, no float ────────────────────────────────────


def test_all_decimal_no_float() -> None:
    """All financial fields must be Decimal — no float anywhere."""
    calc = AcbCalculator(asset="BTC")
    lot = calc.add_acquisition(
        quantity=Decimal("1"),
        cost_usd=Decimal("50000"),
        exchange_rate=Decimal("1.30"),
    )
    assert isinstance(lot.quantity, Decimal)
    assert isinstance(lot.cost_usd, Decimal)
    assert isinstance(lot.cost_cad, Decimal)
    assert isinstance(lot.exchange_rate, Decimal)
    assert isinstance(calc.acb_per_unit, Decimal)

    disp = calc.record_disposition(
        quantity=Decimal("1"),
        proceeds_usd=Decimal("60000"),
        exchange_rate=Decimal("1.35"),
    )
    assert isinstance(disp.proceeds_cad, Decimal)
    assert isinstance(disp.total_acb_cad, Decimal)
    assert isinstance(disp.gain_loss_cad, Decimal)
    assert isinstance(disp.acb_per_unit_cad, Decimal)


# ── Test 10 — Anti-regression: no banned libraries ────────────────────


def test_no_banned_libraries_in_tax_module() -> None:
    """Grep: no banned imports in the tax module source files."""
    from pathlib import Path
    module_dir = Path(__file__).parent
    for py_file in module_dir.glob("*.py"):
        if py_file.name.startswith("test_"):
            continue
        content = py_file.read_text()
        for banned in ("import json", "import pandas", "aioredis",
                        "import pickle", "sqlalchemy", "import requests"):
            assert banned not in content, (
                "Banned import '{}' found in {}".format(
                    banned, py_file.name,
                )
            )
