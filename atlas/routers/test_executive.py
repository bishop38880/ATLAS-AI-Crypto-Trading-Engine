import pytest
from unittest.mock import AsyncMock
import httpx
import msgspec
from datetime import datetime, timezone, timedelta

from backend.main import app
from atlas.dependencies import get_redis

# Basic tests for executive intelligence router

@pytest.fixture
def mock_redis():
    mock = AsyncMock()
    mock.smembers = AsyncMock(return_value=set())
    return mock

@pytest.fixture
async def client(mock_redis):
    app.dependency_overrides[get_redis] = lambda: mock_redis
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

@pytest.mark.asyncio
async def test_analyze_asset_returns_cached_signal(client, mock_redis):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    cached_signal = {
        "schema_version": "2.0.0",
        "signal_id": "sig-001",
        "asset": "BTC",
        "expires_at": expires_at.isoformat(),
    }
    mock_redis.get.return_value = msgspec.json.encode(cached_signal)

    response = await client.post("/api/executive/analyze/BTC")

    assert response.status_code == 200
    body = response.json()
    assert body["asset"] == "BTC"
    assert body["signal"]["signal_id"] == "sig-001"
    mock_redis.get.assert_called_once_with("polaris:signals:BTC")


@pytest.mark.asyncio
async def test_analyze_asset_falls_back_to_usdt_pair(client, mock_redis):
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
    cached_signal = {
        "schema_version": "2.0.0",
        "signal_id": "sig-002",
        "asset": "BTC/USDT",
        "expires_at": expires_at.isoformat(),
    }
    mock_redis.get.side_effect = [
        None,
        msgspec.json.encode(cached_signal),
    ]

    response = await client.post("/api/executive/analyze/BTC")

    assert response.status_code == 200
    body = response.json()
    assert body["asset"] == "BTC/USDT"
    assert body["signal"]["signal_id"] == "sig-002"
    assert mock_redis.get.await_args_list[0].args == ("polaris:signals:BTC",)
    assert mock_redis.get.await_args_list[1].args == ("polaris:signals:BTC/USDT",)


@pytest.mark.asyncio
async def test_analyze_asset_returns_404_when_absent(client, mock_redis):
    mock_redis.get.return_value = None

    response = await client.post("/api/executive/analyze/BTC")

    assert response.status_code == 404
    assert response.json()["detail"] == "no_cached_signal_for_BTC"


async def _async_keys(keys: list[str]):
    for key in keys:
        yield key


@pytest.mark.asyncio
async def test_analysis_monitor_hydrates_active_33_before_scan(client, mock_redis):
    """Dashboard ladder symbols must hydrate even when key-scan yields nothing."""
    cached_btc = {
        "asset": "BTC/USDT",
        "timestamp": "2026-05-06T20:00:00+00:00",
        "decision": "Hold",
    }
    cached_eth = {
        "asset": "ETH/USDT",
        "timestamp": "2026-05-06T21:00:00+00:00",
        "decision": "Hold",
    }
    mock_redis.smembers = AsyncMock(return_value={b"BTCUSDT", b"ETHUSDT"})
    mock_redis.scan_iter = lambda match: _async_keys([])

    async def get_side_effect(key: str):
        mapping = {
            "polaris:signals:BTCUSDT": msgspec.json.encode(cached_btc),
            "polaris:signals:ETHUSDT": msgspec.json.encode(cached_eth),
        }
        return mapping.get(key)

    mock_redis.get = AsyncMock(side_effect=get_side_effect)

    response = await client.get("/api/executive/analysis-monitor")

    assert response.status_code == 200
    body = response.json()
    assert body["asset_count"] == 2
    assets = {row["asset"] for row in body["analyses"]}
    assert assets == {"BTC/USDT", "ETH/USDT"}


@pytest.mark.asyncio
async def test_analysis_monitor_returns_llm_and_agent_outputs(client, mock_redis):
    cached_signal = {
        "asset": "BTC",
        "timestamp": "2026-05-06T20:00:00+00:00",
        "decision": "Buy",
        "score": 72,
        "raw_confluence_score": 158,
        "confidence": "0.82",
        "pipeline_confidence": "0.77",
        "confidence_tier": "STANDARD",
        "reasoning_summary": "Momentum and derivatives agree.",
        "key_convergences": ["TREND_CONFIRMATION"],
        "key_risks": ["FUNDING_CROWDED"],
        "deepseek_evaluation": {
            "decision": "Buy",
            "confidence": "0.86",
            "cross_correlation_grade": "STANDARD",
            "reasoning": "LLM synthesis confirms the setup.",
            "would_change_if": "Momentum breaks down.",
            "key_convergences": ["RISK_ALIGNED"],
            "key_risks": ["VOLATILITY_SPIKE"],
        },
        "agent_breakdown": {
            "technical": {
                "agent_name": "technical",
                "score": 44,
                "max_score": 55,
                "direction": "bullish",
                "explanation": "Trend support held.",
                "convergences": ["RSI_RECOVERY"],
                "risks": [],
                "sub_signals": {
                    "rsi": {"value": "58", "flag": "BULLISH"},
                },
            },
        },
    }
    mock_redis.scan_iter = lambda match: _async_keys(["polaris:signals:BTC"])
    mock_redis.get.return_value = msgspec.json.encode(cached_signal)

    response = await client.get("/api/executive/analysis-monitor")

    assert response.status_code == 200
    body = response.json()
    analysis = body["analyses"][0]
    assert body["asset_count"] == 1
    assert analysis["asset"] == "BTC"
    assert analysis["llm"]["reasoning"] == "LLM synthesis confirms the setup."
    assert analysis["agents"][0]["subSignals"][0]["flag"] == "BULLISH"


@pytest.mark.asyncio
async def test_analysis_monitor_falls_back_to_latest_signal(client, mock_redis):
    cached_signal = {
        "asset": "ETH",
        "timestamp": "2026-05-06T20:00:00+00:00",
        "decision": "Hold",
    }
    mock_redis.scan_iter = lambda match: _async_keys([])
    mock_redis.get.return_value = msgspec.json.encode(cached_signal)

    response = await client.get("/api/executive/analysis-monitor")

    assert response.status_code == 200
    body = response.json()
    assert body["asset_count"] == 1
    assert body["analyses"][0]["asset"] == "ETH"
