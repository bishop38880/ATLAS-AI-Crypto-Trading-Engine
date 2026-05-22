from decimal import Decimal
import time
import pytest
from pydantic import BaseModel, ConfigDict
from fakeredis import FakeAsyncRedis
from typing import AsyncGenerator

from atlas.core.validation_gate import ValidationGate


class MockSchema(BaseModel):
    model_config = ConfigDict(frozen=True)
    provider: str
    asset: str
    metric: str
    value: Decimal
    timestamp: float
    validation_flags: list[str]


@pytest.fixture
async def redis_client() -> AsyncGenerator[FakeAsyncRedis, None]:
    client = FakeAsyncRedis()
    yield client
    await client.aclose()


@pytest.fixture
def gate(redis_client: FakeAsyncRedis) -> ValidationGate:
    return ValidationGate(redis_client)


@pytest.mark.asyncio
async def test_schema_failure_short_circuits(gate: ValidationGate) -> None:
    """Test that schema failure prevents further processing."""
    class StrictSchema(MockSchema):
        value: Decimal
        extra_field: int
        
    res = await gate.validate(
        schema=StrictSchema,
        provider="pyth",
        asset="btc",
        metric="price",
        value=Decimal("50000.0"),
        timestamp=time.time(),
        source_timestamp=time.time(),
    )
    assert res is None


async def _seed_history(gate: ValidationGate, current_time: float) -> None:
    """Provide 15 points of history to establish P90 interval."""
    for i in range(15):
        await gate.validate(
            schema=MockSchema, provider="pyth", asset="btc",
            metric="spot_price",
            value=Decimal("50000.0") + Decimal(str(i)),
            timestamp=current_time - 1.5 + (i * 0.1),
            source_timestamp=current_time - 1.5 + (i * 0.1),
        )


async def _seed_anomaly_history(gate: ValidationGate) -> None:
    """Provide 14 additional points before the anomaly test."""
    for i in range(14):
        await gate.validate(
            schema=MockSchema, provider="pyth", asset="btc",
            metric="spot_price",
            value=Decimal("50016.0") + Decimal(str(i)),
            timestamp=time.time(), source_timestamp=time.time(),
        )


@pytest.mark.asyncio
async def test_full_pipeline_valid_point(gate: ValidationGate) -> None:
    """Test that a valid, non-stale point passes validation."""
    await _seed_history(gate, time.time())
    res = await gate.validate(
        schema=MockSchema, provider="pyth", asset="btc",
        metric="spot_price", value=Decimal("50015.0"),
        timestamp=time.time(), source_timestamp=time.time(),
    )
    assert res is not None
    assert not res.validation_flags


@pytest.mark.asyncio
async def test_full_pipeline_stale_point(gate: ValidationGate) -> None:
    """Test that a stale point is rejected."""
    await _seed_history(gate, time.time())
    res_stale = await gate.validate(
        schema=MockSchema, provider="pyth", asset="btc",
        metric="spot_price", value=Decimal("50016.0"),
        timestamp=time.time() - 5.0, source_timestamp=time.time() - 5.0,
    )
    assert res_stale is None


@pytest.mark.asyncio
async def test_full_pipeline_anomaly(gate: ValidationGate) -> None:
    """Test that an extreme outlier triggers a hard block."""
    await _seed_history(gate, time.time())
    await _seed_anomaly_history(gate)
    res_anomaly = await gate.validate(
        schema=MockSchema, provider="pyth", asset="btc",
        metric="spot_price", value=Decimal("900000.0"),
        timestamp=time.time(), source_timestamp=time.time(),
    )
    # 900000 vs ~50000 median triggers hard_block → returns None
    assert res_anomaly is None


@pytest.mark.asyncio
async def test_full_pipeline_consistency(gate: ValidationGate) -> None:
    """Test that cross-provider divergence is flagged."""
    await _seed_history(gate, time.time())
    res_cons = await gate.validate(
        schema=MockSchema, provider="pyth", asset="btc",
        metric="spot_price", value=Decimal("50000.0"),
        timestamp=time.time(), source_timestamp=time.time(),
        all_provider_values={"coinalyze": Decimal("50500.0")},
    )
    assert res_cons is not None
    assert "CONSISTENCY_DIVERGENCE" in res_cons.validation_flags
