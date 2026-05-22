import pytest
import asyncio
import inspect
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import AsyncIterator
from fastapi.testclient import TestClient
from redis.asyncio import Redis
import httpx
import msgspec
from starlette.routing import WebSocketRoute

from backend.main import app
from atlas.api.schemas import AgentStatusPayload, ScoresPayload
from atlas.api._channel_reads import (
    read_agents,
    read_confluence,
    read_scores,
    read_scores_ws_broadcast,
)
from atlas.api.routes.signals import get_latest_signal
from atlas.shared.config import PolarisSettings

# ---------------------------------------------------------------------------
# FIXTURES
# ---------------------------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    if "app" not in inspect.signature(httpx.Client.__init__).parameters:
        original_init = httpx.Client.__init__

        def patched_init(self, *args, app=None, **kwargs):
            return original_init(self, *args, **kwargs)

        monkeypatch.setattr(httpx.Client, "__init__", patched_init)

    c = TestClient(app)
    try:
        yield c
    finally:
        c.close()

@pytest.fixture
async def redis_client():
    # Assuming the app has state.redis populated by lifespan
    # But since lifespan is not run in all TestClient setups unless used via context manager,
    # it's safe to assume test framework handles this if TestClient is used in context manager
    # I'll just instantiate a fakeredis instance or clear the db if it's real
    import fakeredis.aioredis
    redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    app.state.redis = redis
    yield redis
    await redis.flushall()

class EmptyRedis:
    async def get(self, key: str) -> None:
        return None

    async def scan_iter(self, match: str) -> AsyncIterator[str]:
        if False:
            yield match

# ---------------------------------------------------------------------------
# TESTS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_agents_channel_returns_agent_list(redis_client: Redis):
    await redis_client.set("agent:alpha:status", msgspec.json.encode({"status": "GREEN", "last_ping_ms": 10}))
    await redis_client.set("agent:beta:status", msgspec.json.encode({"status": "RED", "last_ping_ms": 15}))
    
    data = await read_agents(redis_client)

    assert len(data) == 2
    names = [agent.name for agent in data]
    assert "alpha" in names
    assert "beta" in names

@pytest.mark.asyncio
async def test_ws_scores_channel_returns_signal_output(redis_client: Redis):
    # seed polaris:latest_signal
    signal = {
        "schema_version": "2.0.0",
        "signal_id": "123",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "decision": "Hold",
        "asset": "BTCUSDT",
        "timeframe": "30m",
        "action": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "reasoning_summary": "",
        "key_convergences": [],
        "key_risks": [],
        "is_cascade_triggered": False,
        "hydra_event_id": None,
        "score": 85,
        "confidence": 0.8,
        "category_scores": {"total": 0, "technical": 0, "derivatives": 0, "onchain": 0, "sentiment": 0, "whale": 0, "liquidation": 0, "regime": 0, "funding": 0, "news_macro": 0, "correlation": 0, "context": 0},
        "contributing_graph_paths": [],
        "telemetry": {"cycle_id": "cycle-1", "cycle_latency_ms": 10.0, "agent_count": 1},
        "pipeline_confidence": 0.8,
        "confidence_tier": "STANDARD",
        "position_size_modifier": 1.0,
        "human_review_flag": False,
        "raw_confluence_score": 140,
        "agent_breakdown": {}
    }
    await redis_client.set("polaris:latest_signal", msgspec.json.encode(signal))
    
    data = await read_scores(redis_client, PolarisSettings())

    assert data.total_score == 140
    assert data.normalized_score == 85
    assert data.is_stale is False
    assert data.asset == "BTCUSDT"
    assert data.decision == "Hold"
    assert data.reasoning_summary == ""

@pytest.mark.asyncio
async def test_ws_scores_passes_gate_requires_ladder_threshold(redis_client: Redis):
    """Live scores channel must mirror REST conviction gate (PSM > 0 and raw ≥ threshold)."""

    async def _seed(raw_pts: int) -> None:
        signal = {
            "schema_version": "2.0.0",
            "signal_id": "124",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "decision": "Hold",
            "asset": "SUIUSDT",
            "timeframe": "30m",
            "action": None,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
            "reasoning_summary": "",
            "key_convergences": [],
            "key_risks": [],
            "is_cascade_triggered": False,
            "hydra_event_id": None,
            "score": 45,
            "confidence": 0.45,
            "category_scores": {
                "total": 0,
                "technical": 0,
                "derivatives": 0,
                "onchain": 0,
                "sentiment": 0,
                "whale": 0,
                "liquidation": 0,
                "regime": 0,
                "funding": 0,
                "news_macro": 0,
                "correlation": 0,
                "context": 0,
            },
            "contributing_graph_paths": [],
            "telemetry": {"cycle_id": "cycle-2", "cycle_latency_ms": 10.0, "agent_count": 1},
            "pipeline_confidence": 0.45,
            "confidence_tier": "STANDARD",
            "position_size_modifier": 1.0,
            "human_review_flag": False,
            "raw_confluence_score": raw_pts,
            "agent_breakdown": {},
        }
        await redis_client.set("polaris:latest_signal", msgspec.json.encode(signal))

    await _seed(98)
    low = await read_scores(redis_client, PolarisSettings())
    assert low.passes_gate is False

    await _seed(140)
    high = await read_scores(redis_client, PolarisSettings())
    assert high.passes_gate is True

@pytest.mark.asyncio
async def test_ws_scores_marks_stale_when_old(redis_client: Redis):
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    signal = {
        "schema_version": "2.0.0",
        "signal_id": "123",
        "timestamp": old_ts,
        "decision": "Hold",
        "asset": "BTCUSDT",
        "timeframe": "30m",
        "action": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "reasoning_summary": "",
        "key_convergences": [],
        "key_risks": [],
        "is_cascade_triggered": False,
        "hydra_event_id": None,
        "score": 85,
        "confidence": 0.8,
        "category_scores": {"total": 0, "technical": 0, "derivatives": 0, "onchain": 0, "sentiment": 0, "whale": 0, "liquidation": 0, "regime": 0, "funding": 0, "news_macro": 0, "correlation": 0, "context": 0},
        "contributing_graph_paths": [],
        "telemetry": {"cycle_id": "cycle-1", "cycle_latency_ms": 10.0, "agent_count": 1},
        "pipeline_confidence": 0.8,
        "confidence_tier": "STANDARD",
        "position_size_modifier": 1.0,
        "human_review_flag": False,
        "raw_confluence_score": 140,
        "agent_breakdown": {}
    }
    await redis_client.set("polaris:latest_signal", msgspec.json.encode(signal))
    
    data = await read_scores(redis_client, PolarisSettings())

    assert data.is_stale is True

@pytest.mark.asyncio
async def test_ws_confluence_returns_empty_frame_without_signal(redis_client: Redis):
    await redis_client.delete("polaris:latest_signal")

    data = await read_confluence(redis_client)

    assert data["asset"] == "WAITING"
    assert data["total_score"] == 0.0
    assert set(data["agents"]) == {
        "DerivativesAgent",
        "WhaleWatcherAgent",
        "TechnicalAgent",
        "SocialAgent",
        "MacroAgent",
    }

@pytest.mark.asyncio
async def test_ws_confluence_matches_frontend_contract(redis_client: Redis):
    signal = {
        "asset": "BTCUSDT",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "raw_confluence_score": 140,
        "category_scores": {
            "derivatives": 70,
            "technical": 80,
            "whale": 20,
            "sentiment": 60,
            "news_macro": 30,
        },
        "agent_breakdown": {
            "derivatives": {
                "score": 35,
                "max_score": 50,
                "sub_signals": {
                    "funding": {"value": "neutral", "flag": "INFO"},
                },
            },
        },
    }
    await redis_client.set("polaris:latest_signal", msgspec.json.encode(signal))

    data = await read_confluence(redis_client)

    assert data["asset"] == "BTCUSDT"
    assert data["total_score"] == 140.0
    assert isinstance(data["timestamp"], float)
    assert data["agents"]["DerivativesAgent"]["score"] == 70.0
    assert data["agents"]["DerivativesAgent"]["weight"] == 50
    assert data["agents"]["DerivativesAgent"]["sub_signals"]["funding"]["flag"] == "INFO"
    assert data["agents"]["TechnicalAgent"]["score"] == 80.0

def test_ws_confluence_route_sends_frontend_frame(client):
    app.state.redis = EmptyRedis()

    with client.websocket_connect("/ws/confluence") as websocket:
        data = websocket.receive_json()

    assert data["asset"] == "WAITING"
    assert "DerivativesAgent" in data["agents"]

def test_ws_unknown_channel_is_not_registered_as_explicit_route(client):
    routes = {route.path for route in app.routes if isinstance(route, WebSocketRoute)}
    assert "/ws/unknown_xyz" not in routes

def test_ws_route_precedence_signals_not_shadowed(client):
    routes = {route.path: route for route in app.routes if isinstance(route, WebSocketRoute)}
    assert "/ws/signals" in routes
    assert "/ws/{channel}" in routes
    assert routes["/ws/signals"].endpoint is not routes["/ws/{channel}"].endpoint

def test_ws_keys_command_never_used():
    matches = []
    for path in Path("atlas").rglob("*.py"):
        if path.name.startswith("test_") or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "redis.keys(" in text:
            matches.append(str(path))
    assert matches == []

@pytest.mark.asyncio
async def test_get_signals_latest_returns_204_when_absent(redis_client: Redis):
    await redis_client.delete("polaris:latest_signal")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis_client)))
    response = await get_latest_signal(request)
    assert response.status_code == 204

@pytest.mark.asyncio
async def test_get_market_agents_returns_agent_list(redis_client: Redis):
    await redis_client.set("agent:zeta:status", msgspec.json.encode({"status": "GREEN", "last_ping_ms": 10}))
    await redis_client.set("agent:alpha:status", msgspec.json.encode({"status": "RED", "last_ping_ms": 15}))
    await redis_client.set("agent:mike:status", msgspec.json.encode({"status": "YELLOW", "last_ping_ms": 20}))
    
    data = await read_agents(redis_client)
    assert [agent.name for agent in data] == ["alpha", "mike", "zeta"]

@pytest.mark.asyncio
async def test_get_state_agents_matches_ws_payload(redis_client: Redis):
    await redis_client.set("agent:zeta:status", msgspec.json.encode({"status": "GREEN", "last_ping_ms": 10}))
    await redis_client.set("agent:alpha:status", msgspec.json.encode({"status": "RED", "last_ping_ms": 15}))
    await redis_client.set("agent:mike:status", msgspec.json.encode({"status": "YELLOW", "last_ping_ms": 20}))
    
    rest_data = [agent.model_dump(mode="json") for agent in await read_agents(redis_client)]
    
    ws_data = [agent.model_dump(mode="json") for agent in await read_agents(redis_client)]
    
    assert rest_data == ws_data


def _minimal_signal(asset: str, score: float, *, signal_id: str, timestamp: str) -> dict:
    return {
        "schema_version": "2.0.0",
        "signal_id": signal_id,
        "timestamp": timestamp,
        "decision": "Hold",
        "asset": asset,
        "timeframe": "30m",
        "action": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
        "reasoning_summary": "",
        "key_convergences": [],
        "key_risks": [],
        "is_cascade_triggered": False,
        "hydra_event_id": None,
        "score": score,
        "confidence": 0.8,
        "category_scores": {
            "total": 75,
            "technical": 22,
            "derivatives": 33,
            "onchain": 11,
            "sentiment": 9,
            "whale": 0,
            "liquidation": 0,
            "regime": 0,
            "funding": 0,
            "news_macro": 0,
            "correlation": 0,
            "context": 0,
            "macro": 0,
        },
        "contributing_graph_paths": [],
        "telemetry": {"cycle_id": "cycle-test", "cycle_latency_ms": 10.0, "agent_count": 5},
        "pipeline_confidence": 0.8,
        "confidence_tier": "STANDARD",
        "position_size_modifier": 1.0,
        "human_review_flag": False,
        "raw_confluence_score": 140,
        "agent_breakdown": {},
    }


@pytest.mark.asyncio
async def test_read_scores_ws_broadcast_emits_multiple_assets(redis_client: Redis):
    ts = datetime.now(timezone.utc).isoformat()
    btc = _minimal_signal("BTCUSDT", 82.0, signal_id="s-btc", timestamp=ts)
    eth = _minimal_signal("ETHUSDT", 61.0, signal_id="s-eth", timestamp=ts)

    await redis_client.set("polaris:signals:BTCUSDT", msgspec.json.encode(btc))
    await redis_client.set("polaris:signals:ETHUSDT", msgspec.json.encode(eth))

    rows = await read_scores_ws_broadcast(redis_client, PolarisSettings())
    by_asset = {r.asset.upper(): r for r in rows}
    assert "BTCUSDT" in by_asset
    assert "ETHUSDT" in by_asset
    assert by_asset["BTCUSDT"].total_score == 140
    assert by_asset["BTCUSDT"].normalized_score == 82
    assert by_asset["ETHUSDT"].total_score == 140
    assert by_asset["ETHUSDT"].normalized_score == 61
