"""Tests for ConsistencyChecker."""

from decimal import Decimal
import pytest

from atlas.core.consistency_checker import ConsistencyChecker


@pytest.fixture
def checker() -> ConsistencyChecker:
    """Provide a ConsistencyChecker instance."""
    return ConsistencyChecker()


@pytest.mark.asyncio
async def test_matching_prices_consistent(checker: ConsistencyChecker) -> None:
    """Test that matching prices are marked as consistent."""
    values = {
        "pyth": Decimal("50000.0"),
        "coinalyze": Decimal("50010.0"),  # 0.02% divergence
    }
    res = await checker.check_consistency("spot_price", values)
    assert res.is_consistent
    assert res.max_divergence_pct == 0.02
    assert not res.divergent_providers


@pytest.mark.asyncio
async def test_divergent_prices_warning(checker: ConsistencyChecker) -> None:
    """Test that 1% divergence on spot_price triggers a warning."""
    values = {
        "pyth": Decimal("50000.0"),
        "coinalyze": Decimal("50500.0"),  # 1.0% divergence
    }
    res = await checker.check_consistency("spot_price", values)
    assert not res.is_consistent
    assert res.max_divergence_pct == 1.0
    assert set(res.divergent_providers) == {"pyth", "coinalyze"}


@pytest.mark.asyncio
async def test_open_interest_threshold(checker: ConsistencyChecker) -> None:
    """Test that open interest uses the 2.0% threshold."""
    # 1.5% divergence should be consistent for open_interest
    values = {
        "coinalyze": Decimal("100000.0"),
        "okx": Decimal("101500.0"),  # 1.5% divergence
    }
    res = await checker.check_consistency("open_interest", values)
    assert res.is_consistent
    
    # 2.5% divergence should be inconsistent
    values = {
        "coinalyze": Decimal("100000.0"),
        "okx": Decimal("102500.0"),  # 2.5% divergence
    }
    res = await checker.check_consistency("open_interest", values)
    assert not res.is_consistent
