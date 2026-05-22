"""Tests for pre-execution risk guards — Session 27 Phase 0.

Covers:
    1. Position risk > 1% capital → REJECTED
    2. Position risk ≤ 1% capital → APPROVED
    3. High funding cost → leverage DOWNGRADED
    4. Normal funding cost → APPROVED (no downgrade)
    5. Cross-margin → REJECTED
    6. Isolated-margin → APPROVED
    7. run_all_guards returns all three results
    8. Leverage downgrade halves but never below 1×
    9. Zero capital → REJECTED
   10. All Decimal — no float contamination

Hermetic: uses ``AsyncMock`` for all external boundaries.
No live API or database calls.  Guards are pure functions —
only need to verify inputs/outputs.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from prometheus.risk.guards import (
    CAPITAL_RISK_CEILING_PCT,
    HIGH_FUNDING_THRESHOLD,
    check_capital_risk,
    check_funding_cost,
    check_margin_mode,
    run_all_guards,
)
from prometheus.risk.models import RiskCheckInput, RiskDecision


# ── Fixture builder ───────────────────────────────────────────────────


def _make_input(**overrides: object) -> RiskCheckInput:
    """Build a RiskCheckInput with sensible defaults."""
    defaults: dict[str, object] = {
        "symbol": "BTCUSDT",
        "side": "long",
        "position_notional_usd": Decimal("100"),
        "portfolio_capital_usd": Decimal("100000"),
        "leverage": Decimal("10"),
        "margin_mode": "isolated",
        "funding_rate_8h": Decimal("0"),
    }
    defaults.update(overrides)
    return RiskCheckInput(**defaults)  # type: ignore[arg-type]


# ── Test 1 — Capital risk > 1% → REJECTED ────────────────────────────


@pytest.mark.asyncio
async def test_capital_risk_above_ceiling_rejected() -> None:
    """Position risk > 1% of portfolio → REJECTED."""
    # $2000 / $100000 = 2% → exceeds 1% ceiling
    inputs = _make_input(
        position_notional_usd=Decimal("2000"),
        portfolio_capital_usd=Decimal("100000"),
    )
    result = await check_capital_risk(inputs)
    assert result.decision == RiskDecision.REJECTED
    assert result.rule_name == "capital_risk"


# ── Test 2 — Capital risk ≤ 1% → APPROVED ────────────────────────────


@pytest.mark.asyncio
async def test_capital_risk_within_ceiling_approved() -> None:
    """Position risk ≤ 1% → APPROVED."""
    # $500 / $100000 = 0.5% → within ceiling
    inputs = _make_input(
        position_notional_usd=Decimal("500"),
        portfolio_capital_usd=Decimal("100000"),
    )
    result = await check_capital_risk(inputs)
    assert result.decision == RiskDecision.APPROVED
    assert result.rule_name == "capital_risk"


# ── Test 3 — High funding cost → leverage DOWNGRADED ─────────────────


@pytest.mark.asyncio
async def test_high_funding_cost_downgrades_leverage() -> None:
    """Funding > 0.1% per 8h → leverage halved."""
    inputs = _make_input(
        leverage=Decimal("20"),
        funding_rate_8h=Decimal("0.005"),  # 0.5% — well above threshold
    )
    result = await check_funding_cost(inputs)
    assert result.decision == RiskDecision.DOWNGRADED
    assert result.rule_name == "funding_cost"
    assert result.original_leverage == Decimal("20")
    assert result.adjusted_leverage == Decimal("10")  # halved


# ── Test 4 — Normal funding cost → APPROVED ──────────────────────────


@pytest.mark.asyncio
async def test_normal_funding_cost_approved() -> None:
    """Funding within threshold → APPROVED, leverage unchanged."""
    inputs = _make_input(
        leverage=Decimal("10"),
        funding_rate_8h=Decimal("0.0001"),  # 0.01% — well below threshold
    )
    result = await check_funding_cost(inputs)
    assert result.decision == RiskDecision.APPROVED
    assert result.adjusted_leverage == Decimal("10")


# ── Test 5 — Cross-margin → REJECTED ─────────────────────────────────


@pytest.mark.asyncio
async def test_cross_margin_rejected() -> None:
    """Cross-margin orders must be REJECTED."""
    inputs = _make_input(margin_mode="crossed")
    result = await check_margin_mode(inputs)
    assert result.decision == RiskDecision.REJECTED
    assert result.rule_name == "margin_mode"
    assert "Cross-margin" in result.reason


# ── Test 6 — Isolated-margin → APPROVED ──────────────────────────────


@pytest.mark.asyncio
async def test_isolated_margin_approved() -> None:
    """Isolated-margin orders must be APPROVED."""
    inputs = _make_input(margin_mode="isolated")
    result = await check_margin_mode(inputs)
    assert result.decision == RiskDecision.APPROVED


# ── Test 7 — run_all_guards returns all three results ─────────────────


@pytest.mark.asyncio
async def test_run_all_guards_returns_all_results() -> None:
    """run_all_guards must return exactly 3 check results."""
    inputs = _make_input()
    results = await run_all_guards(inputs)
    assert len(results) == 3
    rule_names = {r.rule_name for r in results}
    assert rule_names == {"margin_mode", "capital_risk", "funding_cost"}


# ── Test 8 — Leverage downgrade never below 1× ───────────────────────


@pytest.mark.asyncio
async def test_leverage_downgrade_floor_at_one() -> None:
    """Halving 1× leverage must stay at 1× (never go to 0.5×)."""
    inputs = _make_input(
        leverage=Decimal("1"),
        funding_rate_8h=Decimal("0.01"),  # extreme funding
    )
    result = await check_funding_cost(inputs)
    assert result.decision == RiskDecision.DOWNGRADED
    assert result.adjusted_leverage == Decimal("1")  # floor


# ── Test 9 — Zero capital → REJECTED ─────────────────────────────────


@pytest.mark.asyncio
async def test_zero_capital_rejected() -> None:
    """Zero portfolio capital → REJECTED (avoid division by zero)."""
    inputs = _make_input(
        portfolio_capital_usd=Decimal("0"),
        position_notional_usd=Decimal("100"),
    )
    result = await check_capital_risk(inputs)
    assert result.decision == RiskDecision.REJECTED


# ── Test 10 — All Decimal, no float ───────────────────────────────────


@pytest.mark.asyncio
async def test_all_decimal_no_float() -> None:
    """All financial fields in results must be Decimal."""
    inputs = _make_input()
    results = await run_all_guards(inputs)
    for result in results:
        assert isinstance(result.original_leverage, Decimal)
        assert isinstance(result.adjusted_leverage, Decimal)


# ── Test 11 — Anti-regression: no banned libraries ────────────────────


def test_no_banned_libraries_in_risk_module() -> None:
    """Grep: no banned imports in the risk module source files."""
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
