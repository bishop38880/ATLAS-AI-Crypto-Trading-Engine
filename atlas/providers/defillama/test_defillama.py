"""Tests for DeFi Llama MCP integration."""

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from atlas.providers.defillama.connector import (
    DefiLlamaMCPConnector,
    _parse_chain_tvl,
    _parse_protocol_tvls,
    _parse_stablecoin_supply,
    _parse_yield_pools,
    _to_decimal,
)
from atlas.providers.defillama.models import ChainTVL, StablecoinSupply
from atlas.providers.defillama.provider import DefiLlamaProvider
from atlas.shared.config import PolarisSettings

# 1. test_to_decimal_valid_string
def test_to_decimal_valid_string():
    assert _to_decimal("123.45") == Decimal("123.45")
    assert _to_decimal(100) == Decimal("100")
    assert _to_decimal(10.5) == Decimal("10.5")

# 2. test_to_decimal_none_returns_zero
def test_to_decimal_none_returns_zero():
    assert _to_decimal(None) == Decimal("0")
    assert _to_decimal(None, default="10") == Decimal("10")

# 3. test_to_decimal_invalid_returns_zero
def test_to_decimal_invalid_returns_zero():
    assert _to_decimal("invalid") == Decimal("0")
    assert _to_decimal({}, default="1") == Decimal("1")
    assert _to_decimal(float("inf")) == Decimal("0")

# 4. test_parse_chain_tvl_correct_fields
def test_parse_chain_tvl_correct_fields():
    raw = {"tvl": 1000, "change_1d": "5.5"}
    parsed = _parse_chain_tvl(raw, "Ethereum")
    assert parsed.chain == "Ethereum"
    assert parsed.tvl_usd == Decimal("1000")
    assert parsed.tvl_change_1d_pct == Decimal("5.5")
    assert parsed.fetched_at_ms > 0

# 5. test_parse_protocol_tvls_respects_limit
def test_parse_protocol_tvls_respects_limit():
    raw = [
        {"name": f"Protocol {i}", "tvl": i} for i in range(10)
    ]
    parsed = _parse_protocol_tvls(raw, limit=3)
    assert len(parsed) == 3
    assert parsed[0].protocol == "Protocol 0"

# 6. test_parse_stablecoin_supply_dominance_calculation
def test_parse_stablecoin_supply_dominance_calculation():
    raw = {
        "totalCirculatingUSD": {"peggedUSD": 1000},
        "usdt_mcap": 600,
        "usdc_mcap": 300
    }
    parsed = _parse_stablecoin_supply(raw)
    assert parsed.total_mcap_usd == Decimal("1000")
    assert parsed.usdt_mcap_usd == Decimal("600")
    assert parsed.usdc_mcap_usd == Decimal("300")
    assert parsed.usdt_dominance_pct == Decimal("60")

# 7. test_parse_stablecoin_supply_zero_total
def test_parse_stablecoin_supply_zero_total():
    raw = {
        "totalCirculatingUSD": {"peggedUSD": 0},
        "usdt_mcap": 0,
        "usdc_mcap": 0
    }
    parsed = _parse_stablecoin_supply(raw)
    assert parsed.usdt_dominance_pct == Decimal("0")

# 8. test_parse_yield_pools_filtering_and_sorting
def test_parse_yield_pools_filtering_and_sorting():
    raw = [
        {"pool": "p1", "tvlUsd": 500000, "apy": 10.0},
        {"pool": "p2", "tvlUsd": 2000000, "apy": 5.0},
        {"pool": "p3", "tvlUsd": 1500000, "apy": 15.0},
    ]
    parsed = _parse_yield_pools(raw, limit=5, min_tvl=1000000.0)
    assert len(parsed) == 2
    assert parsed[0].pool_id == "p3"  # Higher APY first
    assert parsed[1].pool_id == "p2"

# 9. test_parse_yield_pools_respects_limit
def test_parse_yield_pools_respects_limit():
    raw = [
        {"pool": f"p{i}", "tvlUsd": 2000000, "apy": float(i)} for i in range(10)
    ]
    parsed = _parse_yield_pools(raw, limit=4, min_tvl=1000000.0)
    assert len(parsed) == 4
    assert parsed[0].apy == Decimal("9.0")

# 10. test_connector_health_degraded_on_error
@pytest.mark.asyncio
async def test_connector_health_degraded_on_error():
    settings = PolarisSettings()
    connector = DefiLlamaMCPConnector(settings)
    assert connector.get_health()["status"] == "healthy"
    connector._handle_error("test", Exception("boom"))
    assert connector.get_health()["status"] == "degraded"

# 11. test_connector_fetch_chain_tvl_handles_exception
@pytest.mark.asyncio
async def test_connector_fetch_chain_tvl_handles_exception():
    settings = PolarisSettings()
    connector = DefiLlamaMCPConnector(settings)
    with patch.object(connector, "_call_mcp_tool", side_effect=Exception("API error")):
        res = await connector.fetch_chain_tvl("Ethereum")
        assert res is None
        assert connector._error_count == 1

# 12. test_connector_fetch_stablecoin_supply_handles_exception
@pytest.mark.asyncio
async def test_connector_fetch_stablecoin_supply_handles_exception():
    settings = PolarisSettings()
    connector = DefiLlamaMCPConnector(settings)
    with patch.object(connector, "_call_mcp_tool", side_effect=Exception("API error")):
        res = await connector.fetch_stablecoin_supply()
        assert res is None

# 13. test_provider_fetch_data_all_degraded
@pytest.mark.asyncio
async def test_provider_fetch_data_all_degraded():
    r = AsyncMock()
    r.get.return_value = None
    settings = PolarisSettings()
    provider = DefiLlamaProvider(r, settings)
    
    with patch.object(provider._connector, "fetch_chain_tvl", return_value=None):
        with patch.object(provider._connector, "fetch_protocol_tvls", return_value=None):
            with patch.object(provider._connector, "fetch_stablecoin_supply", return_value=None):
                with patch.object(provider._connector, "fetch_yield_pools", return_value=None):
                    ctx = await provider.fetch_data("Ethereum")
                    assert ctx.chain_tvl is None
                    assert ctx.stablecoin_supply is None

# 14. test_provider_returns_cached_data
@pytest.mark.asyncio
async def test_provider_returns_cached_data():
    r = AsyncMock()
    import msgspec
    
    cached_obj = ChainTVL("Ethereum", Decimal("100"), Decimal("1"), 123)
    r.get.return_value = msgspec.json.encode(cached_obj)
    
    settings = PolarisSettings()
    provider = DefiLlamaProvider(r, settings)
    
    with patch.object(provider._connector, "fetch_chain_tvl") as mock_fetch:
        ctx = await provider.fetch_data("Ethereum")
        assert mock_fetch.call_count == 0
        assert ctx.chain_tvl is not None
        assert ctx.chain_tvl.chain == "Ethereum"

# 15. test_provider_cache_miss_writes_to_redis
@pytest.mark.asyncio
async def test_provider_cache_miss_writes_to_redis():
    r = AsyncMock()
    r.get.return_value = None
    settings = PolarisSettings()
    provider = DefiLlamaProvider(r, settings)
    
    mock_supply = StablecoinSupply(Decimal("100"), Decimal("50"), Decimal("30"), Decimal("50"), 123)
    
    with patch.object(provider._connector, "fetch_stablecoin_supply", return_value=mock_supply):
        await provider.fetch_data("Ethereum")
        assert r.setex.call_count > 0
