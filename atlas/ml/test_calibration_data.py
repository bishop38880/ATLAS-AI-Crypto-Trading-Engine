import pytest
from datetime import datetime, timezone
from atlas.ml.calibration_data import CalibrationDataBuilder, InsufficientDataError


class MockRecord(dict):
    def __init__(self, score, pnl_pct, timestamp):
        super().__init__()
        self["score"] = score
        self["pnl_pct"] = pnl_pct
        self["timestamp"] = timestamp


class MockConnection:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, query, timeout):
        return self.rows


class MockPool:
    def __init__(self, rows):
        self.rows = rows

    def acquire(self):
        class ContextManager:
            def __init__(self, rows):
                self.rows = rows
            async def __aenter__(self):
                return MockConnection(self.rows)
            async def __aexit__(self, exc_type, exc, tb):
                pass
        return ContextManager(self.rows)

    async def close(self):
        pass


@pytest.fixture
def mock_asyncpg():
    def _mock_create_pool(rows):
        return MockPool(rows)
    return _mock_create_pool


@pytest.mark.asyncio
async def test_calibration_data_builder_success(mock_asyncpg):
    rows = [
        MockRecord(80, 0.5, datetime(2023, 1, 1, tzinfo=timezone.utc)),
        MockRecord(60, -0.1, datetime(2023, 1, 2, tzinfo=timezone.utc)),
        MockRecord(90, 1.2, datetime(2023, 1, 3, tzinfo=timezone.utc)),
    ]
    pool = mock_asyncpg(rows)
    
    builder = CalibrationDataBuilder(pool)
    dataset = await builder.build(min_samples=2)
    
    assert dataset.n_samples == 3
    assert dataset.raw_scores == [80.0, 60.0, 90.0]
    assert dataset.outcomes == [1, 0, 1]
    assert dataset.date_range[0] == datetime(2023, 1, 1, tzinfo=timezone.utc)
    assert dataset.date_range[1] == datetime(2023, 1, 3, tzinfo=timezone.utc)
    
    assert all(isinstance(x, float) for x in dataset.raw_scores)
    assert all(isinstance(x, int) for x in dataset.outcomes)


@pytest.mark.asyncio
async def test_calibration_data_builder_insufficient_data(mock_asyncpg):
    rows = [MockRecord(80, 0.5, datetime(2023, 1, 1, tzinfo=timezone.utc))]
    pool = mock_asyncpg(rows)
    
    builder = CalibrationDataBuilder(pool)
    with pytest.raises(InsufficientDataError):
        await builder.build(min_samples=2)
