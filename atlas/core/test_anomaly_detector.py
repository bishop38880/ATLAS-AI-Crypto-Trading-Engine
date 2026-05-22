"""Tests for the AnomalyDetector class."""

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from typing import AsyncGenerator

import pytest
from fakeredis import FakeAsyncRedis
import numpy as np

from atlas.core.anomaly_detector import AnomalyDetector


@pytest.fixture
async def redis_client() -> AsyncGenerator[FakeAsyncRedis, None]:
    """Provide a fake async Redis client."""
    client = FakeAsyncRedis()
    yield client
    await client.aclose()


@pytest.fixture
def detector(redis_client: FakeAsyncRedis) -> AnomalyDetector:
    """Provide an AnomalyDetector instance."""
    return AnomalyDetector(redis_client=redis_client)


@pytest.mark.asyncio
async def test_fallback_mad_z_score(detector: AnomalyDetector) -> None:
    """Test fallback to MAD Z-score when points < 30."""
    # Insert 20 normal points
    for i in range(20):
        res = await detector.check_anomaly("pyth", "btc", "price", 50000.0 + i)
        assert not res.is_anomaly
        assert not res.hard_block
        assert isinstance(res.mad_z_score, float)
        assert type(res.mad_z_score) is float

    # Insert an extreme outlier to trigger hard block
    res = await detector.check_anomaly("pyth", "btc", "price", 90000.0)
    assert res.is_anomaly
    assert res.hard_block
    assert res.anomaly_score == 0.0  # Z-score fallback


@pytest.mark.asyncio
async def test_isolation_forest_anomaly(detector: AnomalyDetector) -> None:
    """Test Isolation Forest triggers on extreme outlier when points >= 30."""
    import random
    random.seed(42)
    # Insert 50 normal points
    for i in range(50):
        # random data around 50000
        val = 50000.0 + random.uniform(-1000, 1000)
        await detector.check_anomaly("pyth", "btc", "price", val)

    # The 51st point is an outlier
    res = await detector.check_anomaly("pyth", "btc", "price", 900000.0)
    assert res.is_anomaly
    assert res.hard_block  # Should also trigger hard block because it's extreme
    assert res.anomaly_score < 0.0
    assert type(res.anomaly_score) is float

@pytest.mark.asyncio
async def test_dual_thresholds_volatile_regime(detector: AnomalyDetector, redis_client: FakeAsyncRedis) -> None:
    """Test regime-conditional thresholds."""
    # Set volatile regime
    await redis_client.set("market:volatility_regime", "high_volatility")
    
    # Insert points
    for i in range(20):
        await detector.check_anomaly("pyth", "btc", "price", 50000.0)
        
    # Anomaly with MAD Z between 5.0 and 8.0 should flag but not hard block
    # MAD is 0 initially, wait, if mad is 0, mad_z is 0. 
    # Let's insert a variance
    await redis_client.flushall()
    await redis_client.set("market:volatility_regime", "high_volatility")
    for i in range(20):
        await detector.check_anomaly("pyth", "btc", "price", 50000.0 + (i % 2))
        
    # median = 50001.0, mad = 1.0
    # mad_z = 0.6745 * (value - 50001.0)
    # For mad_z ~ 6.07 (flag only), value ~ 50010.0
    res = await detector.check_anomaly("pyth", "btc", "price", 50010.0)
    assert res.is_anomaly
    assert not res.hard_block
    
    # For mad_z ~ 9.44 (hard block), value ~ 50015.0
    res = await detector.check_anomaly("pyth", "btc", "price", 50015.0)
    assert res.is_anomaly
    assert res.hard_block


@pytest.mark.asyncio
async def test_sklearn_wrapped_in_to_thread(detector: AnomalyDetector) -> None:
    """Test that IsolationForest operations are wrapped in to_thread."""
    # We mock asyncio.to_thread
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        # Provide a fake result for the thread call
        mock_forest = MagicMock()
        
        # When training, it returns a forest. 
        # When predicting, it returns (is_anomaly, score).
        # We use a side_effect to return different things based on the function passed.
        def side_effect(func, *args, **kwargs):
            if func.__name__ == "_train_forest":
                return mock_forest
            elif func.__name__ == "_predict_forest":
                return (True, -0.5)
            return None
            
        mock_to_thread.side_effect = side_effect

        # Insert 30 points so it calls the forest logic
        for i in range(30):
            await detector.check_anomaly("mock", "eth", "price", 1000.0)

        assert mock_to_thread.call_count > 0
        
        # Verify it trained
        calls = mock_to_thread.call_args_list
        train_calls = [c for c in calls if c[0][0].__name__ == "_train_forest"]
        assert len(train_calls) == 1
