"""Unit tests for horizon outcome helpers."""

from __future__ import annotations

from decimal import Decimal

import pytest

from atlas.jobs.outcome_horizon_job import (
    horizon_return_pct,
    normalize_asset_base,
    validate_price_snapshots_table_ident,
)


def test_horizon_return_pct_long_short() -> None:
    entry = Decimal("100")
    exit_px = Decimal("101")
    assert horizon_return_pct("Buy", entry, exit_px) == pytest.approx(1.0)
    assert horizon_return_pct("Strong Sell", entry, exit_px) == pytest.approx(-1.0)


def test_normalize_asset_base_strips_quote() -> None:
    assert normalize_asset_base("BTC/USDT") == "BTC"


def test_validate_price_snapshots_table_ident_rejects_bad() -> None:
    assert validate_price_snapshots_table_ident("price_snapshots") == "price_snapshots"
    with pytest.raises(ValueError):
        validate_price_snapshots_table_ident("bad;drop")
