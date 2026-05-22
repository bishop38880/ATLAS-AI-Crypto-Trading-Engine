"""
Tests for Proof of Reserve & RWA Macro MCP Server.

Section 5 Architecture: Macro Context — Institutional Rotation.
Covers: Pydantic model construction, Decimal precision, regime signal
determination, cache operations, and degraded-mode fallbacks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from .background_poller import (
    _calculate_ratio,
    _empty_por_data,
    _is_oracle_stale,
    _store_poll_results,
    _update_cache_degraded,
    get_all_cached,
    get_cached_flows,
)
from .config import (
    RWA_CONTRACTS,
    SUPPORTED_SYMBOLS,
    ZERO_ADDRESS,
)
from .models import (
    MacroRotationReport,
    PoRHealthReport,
    RegimeSignal,
    RWAFlows,
    RWATokenSummary,
)
from .server import (
    _build_por_report,
    _build_flow_report,
    _build_token_summary,
    _compute_aggregate_velocity,
    _determine_regime,
    _validate_symbol,
)
from .web3_engine import (
    _normalise_uint256,
    _parse_mint_burn_logs,
)


# ───────────────────────────────────────────────────────────────────
# Domain 1: Decimal Precision — The Float Trap
# ───────────────────────────────────────────────────────────────────


class TestNormaliseUint256:
    """Tests for uint256 → Decimal normalisation."""

    def test_six_decimal_token(self) -> None:
        """BUIDL uses 6 decimals — 1_000_000 raw = 1.000000."""
        result: Decimal = _normalise_uint256(1_000_000, 6)
        assert result == Decimal("1")
        assert isinstance(result, Decimal)

    def test_eighteen_decimal_token(self) -> None:
        """USDY uses 18 decimals — 1e18 raw = 1.0."""
        raw: int = 10**18
        result: Decimal = _normalise_uint256(raw, 18)
        assert result == Decimal("1")

    def test_eight_decimal_chainlink(self) -> None:
        """Chainlink oracles typically use 8 decimals."""
        result: Decimal = _normalise_uint256(100_000_000, 8)
        assert result == Decimal("1")

    def test_large_uint256_no_precision_loss(self) -> None:
        """
        Verify no precision loss on large uint256 values.

        100 billion USDY with 18 decimals — this would overflow float64.
        """
        raw: int = 100_000_000_000 * 10**18
        result: Decimal = _normalise_uint256(raw, 18)
        assert result == Decimal("100000000000")
        assert str(result) == "100000000000"

    def test_zero_value(self) -> None:
        """Zero raw value returns Decimal zero."""
        result: Decimal = _normalise_uint256(0, 18)
        assert result == Decimal("0")


# ───────────────────────────────────────────────────────────────────
# Domain 2: Model Construction — Frozen Pydantic
# ───────────────────────────────────────────────────────────────────


class TestPoRHealthReport:
    """Tests for PoRHealthReport model construction."""

    def test_fully_backed_construction(self) -> None:
        """Fully-backed report with valid PoR data."""
        report: PoRHealthReport = PoRHealthReport(
            symbol="BUIDL",
            name="BlackRock BUIDL",
            on_chain_supply=Decimal("500000000"),
            off_chain_reserve=Decimal("510000000"),
            collateralization_ratio=Decimal("1.02"),
            is_fully_backed=True,
            oracle_updated_at=datetime.now(tz=timezone.utc),
            oracle_stale=False,
        )
        assert report.is_fully_backed is True
        assert report.collateralization_ratio == Decimal("1.02")
        assert report.status == "OK"

    def test_undercollateralised_report(self) -> None:
        """Under-collateralised report flags risk correctly."""
        report: PoRHealthReport = PoRHealthReport(
            symbol="USDY",
            name="Ondo USDY",
            on_chain_supply=Decimal("100000000"),
            off_chain_reserve=Decimal("95000000"),
            collateralization_ratio=Decimal("0.95"),
            is_fully_backed=False,
            oracle_updated_at=datetime.now(tz=timezone.utc),
            oracle_stale=False,
        )
        assert report.is_fully_backed is False
        assert report.collateralization_ratio < Decimal("1.0")

    def test_frozen_immutability(self) -> None:
        """Frozen model rejects attribute mutation."""
        report: PoRHealthReport = PoRHealthReport(
            symbol="BUIDL",
            name="BlackRock BUIDL",
            on_chain_supply=Decimal("100"),
            off_chain_reserve=Decimal("100"),
            collateralization_ratio=Decimal("1"),
            is_fully_backed=True,
            oracle_updated_at=datetime.now(tz=timezone.utc),
            oracle_stale=False,
        )
        with pytest.raises(Exception):
            report.symbol = "HACKED"  # type: ignore[misc]


class TestRWAFlows:
    """Tests for RWAFlows model."""

    def test_positive_net_flow(self) -> None:
        """Net inflow when mints exceed burns."""
        flows: RWAFlows = RWAFlows(
            symbol="BUIDL",
            mint_volume_24h=Decimal("50000000"),
            burn_volume_24h=Decimal("10000000"),
            net_flow_24h=Decimal("40000000"),
            mint_volume_7d=Decimal("200000000"),
            burn_volume_7d=Decimal("100000000"),
            net_flow_7d=Decimal("100000000"),
            avg_daily_net_flow_7d=Decimal("14285714.285714"),
        )
        assert flows.net_flow_24h > Decimal("0")
        assert flows.net_flow_7d > Decimal("0")

    def test_negative_net_flow(self) -> None:
        """Net outflow when burns exceed mints."""
        flows: RWAFlows = RWAFlows(
            symbol="USDY",
            mint_volume_24h=Decimal("5000000"),
            burn_volume_24h=Decimal("20000000"),
            net_flow_24h=Decimal("-15000000"),
            mint_volume_7d=Decimal("50000000"),
            burn_volume_7d=Decimal("80000000"),
            net_flow_7d=Decimal("-30000000"),
            avg_daily_net_flow_7d=Decimal("-4285714.285714"),
        )
        assert flows.net_flow_24h < Decimal("0")


class TestMacroRotationReport:
    """Tests for MacroRotationReport model."""

    def test_risk_off_construction(self) -> None:
        """RISK_OFF report with token summaries."""
        report: MacroRotationReport = MacroRotationReport(
            regime_signal=RegimeSignal.RISK_OFF,
            total_net_inflow_24h=Decimal("500000000"),
            total_net_inflow_7d=Decimal("800000000"),
            velocity_ratio_aggregate=Decimal("4.375"),
            token_summaries=[
                RWATokenSummary(
                    symbol="BUIDL",
                    net_flow_24h=Decimal("300000000"),
                    net_flow_7d=Decimal("500000000"),
                    velocity_ratio=Decimal("4.2"),
                    is_fully_backed=True,
                ),
            ],
            reasoning="Test reasoning",
        )
        assert report.regime_signal == RegimeSignal.RISK_OFF
        assert len(report.token_summaries) == 1


# ───────────────────────────────────────────────────────────────────
# Domain 3: Quantitative Logic — Regime Determination
# ───────────────────────────────────────────────────────────────────


class TestRegimeDetermination:
    """Tests for the macro regime signal logic."""

    def test_underbacked_triggers_risk_off(self) -> None:
        """Under-collateralised token triggers RISK_OFF regardless of velocity."""
        summaries: list[RWATokenSummary] = [
            RWATokenSummary(
                symbol="BUIDL",
                net_flow_24h=Decimal("0"),
                net_flow_7d=Decimal("0"),
                velocity_ratio=Decimal("0"),
                is_fully_backed=False,
            ),
        ]
        signal, reasoning = _determine_regime(
            Decimal("0"), Decimal("0"), summaries
        )
        assert signal == RegimeSignal.RISK_OFF
        assert "Undercollateralised" in reasoning

    def test_high_velocity_triggers_risk_off(self) -> None:
        """Velocity > 3x threshold triggers RISK_OFF rotation signal."""
        summaries: list[RWATokenSummary] = [
            RWATokenSummary(
                symbol="BUIDL",
                net_flow_24h=Decimal("100"),
                net_flow_7d=Decimal("100"),
                velocity_ratio=Decimal("4.0"),
                is_fully_backed=True,
            ),
        ]
        signal, reasoning = _determine_regime(
            Decimal("4.0"), Decimal("100"), summaries
        )
        assert signal == RegimeSignal.RISK_OFF
        assert "velocity" in reasoning.lower()

    def test_moderate_inflow_returns_neutral(self) -> None:
        """Moderate velocity (1 < v < 3) returns NEUTRAL."""
        summaries: list[RWATokenSummary] = [
            RWATokenSummary(
                symbol="USDY",
                net_flow_24h=Decimal("50"),
                net_flow_7d=Decimal("200"),
                velocity_ratio=Decimal("1.75"),
                is_fully_backed=True,
            ),
        ]
        signal, _ = _determine_regime(
            Decimal("1.75"), Decimal("50"), summaries
        )
        assert signal == RegimeSignal.NEUTRAL

    def test_low_flow_returns_risk_on(self) -> None:
        """Low or negative flows return RISK_ON — no Treasury rotation."""
        summaries: list[RWATokenSummary] = [
            RWATokenSummary(
                symbol="BUIDL",
                net_flow_24h=Decimal("-10"),
                net_flow_7d=Decimal("-50"),
                velocity_ratio=Decimal("0.5"),
                is_fully_backed=True,
            ),
        ]
        signal, reasoning = _determine_regime(
            Decimal("0.5"), Decimal("-10"), summaries
        )
        assert signal == RegimeSignal.RISK_ON
        assert "risk appetite" in reasoning.lower()


class TestVelocityComputation:
    """Tests for aggregate velocity ratio calculation."""

    def test_normal_velocity(self) -> None:
        """Standard velocity = 24h / (7d / 7)."""
        velocity: Decimal = _compute_aggregate_velocity(
            Decimal("100"), Decimal("350")
        )
        assert velocity == Decimal("2")

    def test_zero_seven_day_returns_zero(self) -> None:
        """Zero 7d flow prevents division by zero."""
        velocity: Decimal = _compute_aggregate_velocity(
            Decimal("100"), Decimal("0")
        )
        assert velocity == Decimal("0")

    def test_negative_seven_day_returns_zero(self) -> None:
        """Negative 7d flow returns zero velocity."""
        velocity: Decimal = _compute_aggregate_velocity(
            Decimal("100"), Decimal("-70")
        )
        assert velocity == Decimal("0")


# ───────────────────────────────────────────────────────────────────
# Domain 4: Cache Operations
# ───────────────────────────────────────────────────────────────────


class TestCacheOperations:
    """Tests for in-memory cache read/write."""

    @pytest.fixture(autouse=True)
    async def _reset_cache(self) -> None:
        """Reset module-level cache before each test."""
        from . import background_poller
        async with background_poller._cache_lock:
            background_poller._cache.clear()

    async def test_store_and_retrieve(self) -> None:
        """Store poll results and retrieve via get_cached_flows."""
        await _store_poll_results(
            symbol="BUIDL",
            total_supply=Decimal("500000000"),
            por_data={
                "off_chain_reserve": Decimal("510000000"),
                "collateralization_ratio": Decimal("1.02"),
                "is_fully_backed": True,
                "oracle_updated_at": datetime.now(tz=timezone.utc),
                "oracle_stale": False,
                "status": "OK",
            },
            flow_24h=(Decimal("50000000"), Decimal("10000000")),
            flow_7d=(Decimal("200000000"), Decimal("100000000")),
        )
        cached: dict[str, Any] | None = await get_cached_flows("BUIDL")
        assert cached is not None
        assert cached["net_flow_24h"] == Decimal("40000000")
        assert cached["net_flow_7d"] == Decimal("100000000")
        assert cached["is_fully_backed"] is True

    async def test_uncached_symbol_returns_none(self) -> None:
        """Uncached symbol returns None — not an error."""
        result: dict[str, Any] | None = await get_cached_flows("NONEXISTENT")
        assert result is None

    async def test_degraded_marks_status(self) -> None:
        """DEGRADED status set without wiping existing good data."""
        await _store_poll_results(
            symbol="USDY",
            total_supply=Decimal("100"),
            por_data=_empty_por_data(),
            flow_24h=(Decimal("0"), Decimal("0")),
            flow_7d=(Decimal("0"), Decimal("0")),
        )
        await _update_cache_degraded("USDY")
        cached: dict[str, Any] | None = await get_cached_flows("USDY")
        assert cached is not None
        assert cached["status"] == "DEGRADED"
        assert cached["total_supply"] == Decimal("100")

    async def test_get_all_cached_returns_snapshot(self) -> None:
        """get_all_cached returns copies, not live references."""
        await _store_poll_results(
            symbol="BUIDL",
            total_supply=Decimal("1"),
            por_data=_empty_por_data(),
            flow_24h=(Decimal("0"), Decimal("0")),
            flow_7d=(Decimal("0"), Decimal("0")),
        )
        snapshot: dict[str, dict[str, Any]] = await get_all_cached()
        assert "BUIDL" in snapshot
        assert isinstance(snapshot["BUIDL"], dict)


# ───────────────────────────────────────────────────────────────────
# Domain 5: Collateralisation Ratio & Oracle Staleness
# ───────────────────────────────────────────────────────────────────


class TestCollateralisationRatio:
    """Tests for ratio calculation and oracle staleness."""

    def test_ratio_above_one(self) -> None:
        """Over-collateralised: ratio > 1.0."""
        ratio: Decimal = _calculate_ratio(Decimal("110"), Decimal("100"))
        assert ratio == Decimal("1.1")

    def test_ratio_below_one(self) -> None:
        """Under-collateralised: ratio < 1.0."""
        ratio: Decimal = _calculate_ratio(Decimal("90"), Decimal("100"))
        assert ratio == Decimal("0.9")

    def test_zero_supply_returns_zero(self) -> None:
        """Zero supply prevents division by zero."""
        ratio: Decimal = _calculate_ratio(Decimal("100"), Decimal("0"))
        assert ratio == Decimal("0")

    def test_oracle_fresh(self) -> None:
        """Oracle updated recently is not stale."""
        result: bool = _is_oracle_stale(datetime.now(tz=timezone.utc))
        assert result is False

    def test_oracle_stale(self) -> None:
        """Oracle older than 24h is stale."""
        from datetime import timedelta
        old: datetime = datetime.now(tz=timezone.utc) - timedelta(hours=25)
        result: bool = _is_oracle_stale(old)
        assert result is True


# ───────────────────────────────────────────────────────────────────
# Domain 6: Transfer Log Parsing
# ───────────────────────────────────────────────────────────────────


class TestParseMintBurnLogs:
    """Tests for Transfer event log parsing."""

    def _make_log(
        self,
        from_addr: str,
        to_addr: str,
        value_raw: int,
    ) -> dict[str, Any]:
        """Build a synthetic Transfer log entry."""
        from_padded: bytes = bytes.fromhex(from_addr[2:].zfill(64))
        to_padded: bytes = bytes.fromhex(to_addr[2:].zfill(64))
        topic0: bytes = bytes.fromhex(
            "ddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
        )
        data: bytes = value_raw.to_bytes(32, "big")
        return {
            "topics": [topic0, from_padded, to_padded],
            "data": data,
        }

    def test_mint_detected(self) -> None:
        """Transfer from zero address = mint."""
        zero: str = "0x" + "0" * 40
        recipient: str = "0x" + "ab" * 20
        log: dict[str, Any] = self._make_log(zero, recipient, 1_000_000)
        minted, burned = _parse_mint_burn_logs([log], 6)
        assert minted == Decimal("1")
        assert burned == Decimal("0")

    def test_burn_detected(self) -> None:
        """Transfer to zero address = burn."""
        sender: str = "0x" + "cd" * 20
        zero: str = "0x" + "0" * 40
        log: dict[str, Any] = self._make_log(sender, zero, 2_000_000)
        minted, burned = _parse_mint_burn_logs([log], 6)
        assert minted == Decimal("0")
        assert burned == Decimal("2")

    def test_normal_transfer_ignored(self) -> None:
        """Normal transfer (non-mint, non-burn) produces zero flows."""
        sender: str = "0x" + "aa" * 20
        recipient: str = "0x" + "bb" * 20
        log: dict[str, Any] = self._make_log(sender, recipient, 5_000_000)
        minted, burned = _parse_mint_burn_logs([log], 6)
        assert minted == Decimal("0")
        assert burned == Decimal("0")

    def test_empty_logs(self) -> None:
        """Empty log list returns zero flows."""
        minted, burned = _parse_mint_burn_logs([], 18)
        assert minted == Decimal("0")
        assert burned == Decimal("0")


# ───────────────────────────────────────────────────────────────────
# Domain 7: Symbol Validation & Config
# ───────────────────────────────────────────────────────────────────


class TestSymbolValidation:
    """Tests for symbol validation and config integrity."""

    def test_valid_symbol_normalised(self) -> None:
        """Valid symbol returns uppercase normalised form."""
        assert _validate_symbol("buidl") == "BUIDL"
        assert _validate_symbol("  usdy  ") == "USDY"

    def test_invalid_symbol_raises(self) -> None:
        """Invalid symbol raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported symbol"):
            _validate_symbol("FAKE_TOKEN")

    def test_supported_symbols_match_contracts(self) -> None:
        """SUPPORTED_SYMBOLS matches RWA_CONTRACTS keys."""
        assert SUPPORTED_SYMBOLS == frozenset(RWA_CONTRACTS.keys())

    def test_all_contracts_have_required_keys(self) -> None:
        """Every contract entry has required fields."""
        required_keys: set[str] = {
            "name", "token_address", "por_oracle_address",
            "chain", "token_decimals", "oracle_decimals",
        }
        for symbol, info in RWA_CONTRACTS.items():
            missing: set[str] = required_keys - set(info.keys())
            assert not missing, (
                f"{symbol} missing keys: {missing}"
            )


# ───────────────────────────────────────────────────────────────────
# Domain 8: Server Tool Builders
# ───────────────────────────────────────────────────────────────────


class TestToolBuilders:
    """Tests for server-side report construction helpers."""

    def _sample_cache_entry(self) -> dict[str, Any]:
        """Return a realistic cache entry for testing."""
        return {
            "total_supply": Decimal("500000000"),
            "off_chain_reserve": Decimal("510000000"),
            "collateralization_ratio": Decimal("1.02"),
            "is_fully_backed": True,
            "oracle_updated_at": datetime.now(tz=timezone.utc),
            "oracle_stale": False,
            "status": "OK",
            "mint_volume_24h": Decimal("50000000"),
            "burn_volume_24h": Decimal("10000000"),
            "net_flow_24h": Decimal("40000000"),
            "mint_volume_7d": Decimal("200000000"),
            "burn_volume_7d": Decimal("100000000"),
            "net_flow_7d": Decimal("100000000"),
            "avg_daily_net_flow_7d": Decimal("14285714"),
        }

    def test_build_por_report(self) -> None:
        """PoR report builder produces valid frozen model."""
        cached: dict[str, Any] = self._sample_cache_entry()
        report: PoRHealthReport = _build_por_report("BUIDL", cached)
        assert report.symbol == "BUIDL"
        assert report.is_fully_backed is True
        assert isinstance(report.on_chain_supply, Decimal)

    def test_build_flow_report(self) -> None:
        """Flow report builder produces valid frozen model."""
        cached: dict[str, Any] = self._sample_cache_entry()
        report: RWAFlows = _build_flow_report("BUIDL", cached)
        assert report.symbol == "BUIDL"
        assert report.net_flow_24h == Decimal("40000000")

    def test_build_token_summary_with_zero_avg(self) -> None:
        """Token summary handles zero avg_daily gracefully."""
        data: dict[str, Any] = {
            "net_flow_24h": Decimal("100"),
            "net_flow_7d": Decimal("0"),
            "avg_daily_net_flow_7d": Decimal("0"),
            "is_fully_backed": True,
        }
        summary: RWATokenSummary = _build_token_summary("BUIDL", data)
        assert summary.velocity_ratio == Decimal("0")
