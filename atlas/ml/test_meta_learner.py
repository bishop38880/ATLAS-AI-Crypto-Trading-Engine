import pytest
import numpy as np
from unittest.mock import AsyncMock, MagicMock
from atlas.ml.meta_learner import StackingMetaLearner, InsufficientDataError
import msgspec

@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.close = AsyncMock()
    return pool, conn

@pytest.mark.asyncio
async def test_build_dataset_insufficient_data(mock_pool):
    pool, conn = mock_pool
    conn.fetch.return_value = [{"agent_breakdown": "{}", "pnl_pct": 0.0}] * 50
    
    learner = StackingMetaLearner(asyncpg_pool=pool)
    with pytest.raises(InsufficientDataError, match="Found 50 samples"):
        await learner.build_dataset(min_samples=200)

@pytest.mark.asyncio
async def test_build_dataset_feature_matrix_shape(mock_pool):
    pool, conn = mock_pool
    
    breakdown = {
        "agent_a": {"score": 50},
        "agent_b": {"score": 20},
    }
    encoded = msgspec.json.encode(breakdown).decode()
    
    rows = [{"agent_breakdown": encoded, "pnl_pct": 5.0} for _ in range(100)] + \
           [{"agent_breakdown": encoded, "pnl_pct": -2.0} for _ in range(100)]
           
    conn.fetch.return_value = rows
    
    learner = StackingMetaLearner(asyncpg_pool=pool)
    X, y = await learner.build_dataset(min_samples=200)
    
    assert X.shape == (200, 10)
    assert y.shape == (200,)
    
    assert X[0][0] == 50.0
    assert X[0][1] == 20.0
    assert y[0] == 1
    assert y[150] == 0

def test_train_and_predict(tmp_path):
    X = np.random.rand(200, 10)
    y = np.random.randint(0, 2, 200)
    
    learner = StackingMetaLearner(asyncpg_pool=MagicMock())
    learner.MODEL_PATH = tmp_path / "model.json"
    
    learner.train(X, y)
    assert learner.model is not None
    assert learner.MODEL_PATH.exists()
    
    learner2 = StackingMetaLearner(asyncpg_pool=MagicMock())
    learner2.MODEL_PATH = tmp_path / "model.json"
    learner2._load_model()
    
    assert learner2.model is not None
    prob = learner2.predict([0.5] * 10)
    assert isinstance(prob, float)
    assert 0.0 <= prob <= 1.0
