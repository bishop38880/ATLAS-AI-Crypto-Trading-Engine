from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List, Dict, Any
import asyncio
from loguru import logger

from atlas.shared.config import PolarisSettings

router = APIRouter(prefix="/ws", tags=["websocket"])

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> bool:
        settings = PolarisSettings()
        if channel not in self.active_connections:
            self.active_connections[channel] = []
            
        if len(self.active_connections[channel]) >= settings.WS_MAX_CONNECTIONS_PER_CHANNEL:
            await websocket.close(code=4429, reason="max connections reached")
            return False
            
        await websocket.accept()
        self.active_connections[channel].append(websocket)
        logger.bind(channel=channel).info("ws_connected")
        return True

    def disconnect(self, websocket: WebSocket, channel: str):
        if channel in self.active_connections and websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
            logger.bind(channel=channel).info("ws_disconnected")

    async def broadcast(self, message: Dict[str, Any], channel: str):
        if channel not in self.active_connections:
            return
        
        dead_connections = []
        for connection in self.active_connections[channel]:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error("broadcast error | channel={} | err={}", channel, str(e))
                dead_connections.append(connection)
        
        for dead in dead_connections:
            self.disconnect(dead, channel)

ws_manager = ConnectionManager()

@router.websocket("/activity")
async def activity_websocket(websocket: WebSocket):
    if not await ws_manager.connect(websocket, "activity"):
        return
    try:
        # Initial stub data if needed
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "activity")

# ─── Stub Emitters for Task 2 ────────────────────────────────

async def emit_agent_activity_stub(agent_name: str, cycle_ts: str):
    """TODO: wire to real agent output"""
    event = {
        "type": "agent_activity",
        "agent_name": agent_name,
        "category": "DERIVATIVES",
        "status": "complete",
        "score": 62,
        "max_points": 75,
        "latency_ms": 182,
        "rag_queries": [f"{agent_name} funding rate analysis"],
        "reasoning_steps": [
            "Logic step 1: analysis complete",
            "Logic step 2: score computed",
        ],
        "anomaly_flags": [],
        "cycle_ts": cycle_ts
    }
    await ws_manager.broadcast(event, "activity")

async def emit_llm_call_stub(caller: str, cycle_ts: str):
    """TODO: wire to real LLM client"""
    event = {
        "type": "llm_call",
        "id": f"call_{asyncio.get_event_loop().time()}",
        "timestamp": cycle_ts,
        "model": "deepseek-v3",
        "tier": "api",
        "caller": caller,
        "prompt_tokens": 612,
        "completion_tokens": 235,
        "total_tokens": 847,
        "latency_ms": 234,
        "tier_reason": None,
        "cycle_ts": cycle_ts
    }
    await ws_manager.broadcast(event, "activity")

async def emit_signal_trace_stub(cycle_id: str, asset: str):
    """TODO: wire to confluence synthesiser"""
    # Simulate a full trace
    for i in range(1, 12):
        step = {
            "type": "signal_trace_step",
            "cycle_id": cycle_id,
            "asset": asset,
            "step": i,
            "label": f"Agent {i} scored",
            "score_delta": 10,
            "running_total": i * 10,
            "max_possible": 220,
            "status": "pass",
            "note": None
        }
        await ws_manager.broadcast(step, "activity")
        await asyncio.sleep(0.2)
    
    complete = {
        "type": "signal_trace_complete",
        "cycle_id": cycle_id,
        "asset": asset,
        "final_score": 110,
        "final_decision": "BUY",
        "started_at": "2026-03-25T09:14:00Z"
    }
    await ws_manager.broadcast(complete, "activity")
