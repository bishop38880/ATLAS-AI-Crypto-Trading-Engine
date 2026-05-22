"""Cross-graph contagion stress trigger for Redis."""

import hashlib
from datetime import datetime, timezone

import msgspec
import redis.asyncio as redis
import torch
from loguru import logger


class StressTriggerEvent(msgspec.Struct, frozen=True):
    """Advisory stress signal written to Redis — NOT a confluence input."""

    asset: str
    contagion_signal: float
    threshold_crossed: float
    computed_at: str
    graph_embeddings_hash: str


def _compute_embedding_hash(
    graph_a_emb: torch.Tensor,
    graph_b_emb: torch.Tensor,
) -> str:
    """Compute SHA-256 hash of fused graph embeddings."""
    hasher = hashlib.sha256()
    hasher.update(graph_a_emb.detach().cpu().numpy().tobytes())
    hasher.update(graph_b_emb.detach().cpu().numpy().tobytes())
    return hasher.hexdigest()


async def publish_stress_trigger(
    asset: str,
    contagion_signal: float,
    threshold: float,
    graph_a_emb: torch.Tensor,
    graph_b_emb: torch.Tensor,
    redis_client: redis.Redis,
) -> None:
    """Publish a StressTriggerEvent to Redis if the threshold is crossed."""
    if contagion_signal <= threshold:
        return

    emb_hash = _compute_embedding_hash(graph_a_emb, graph_b_emb)
    event = StressTriggerEvent(
        asset=asset,
        contagion_signal=contagion_signal,
        threshold_crossed=threshold,
        computed_at=datetime.now(timezone.utc).isoformat(),
        graph_embeddings_hash=emb_hash,
    )

    key = f"atlas:gnn:stress_trigger:{asset}"
    payload = msgspec.json.encode(event)
    await _publish_to_redis(key, payload, redis_client, asset, contagion_signal, emb_hash)


async def _publish_to_redis(
    key: str, payload: bytes, redis_client: redis.Redis,
    asset: str, signal: float, emb_hash: str,
) -> None:
    """Write stress trigger payload to Redis — client is shared, do NOT close it."""
    try:
        await redis_client.set(key, payload, ex=1800)
        logger.info(
            "stress_trigger_published | asset={} | contagion={} | hash={}",
            asset, signal, emb_hash,
        )
    except Exception as e:
        logger.error("stress_trigger_failed | asset={} | error={}", asset, str(e))

