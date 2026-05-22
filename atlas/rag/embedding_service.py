"""Embedding service wrapper for the Two-Tier Semantic Cache.

Provides 1024-dimensional embeddings via Mistral.
"""

from loguru import logger

from atlas.core.embedding_client import MistralEmbeddingClient
from atlas.shared.config import ModelStackConfig


class EmbeddingService:
    """Provides embeddings via Mistral or LM Studio (OpenAI-compatible `/v1/embeddings`)."""

    def __init__(self, config: ModelStackConfig) -> None:
        """Initialize the EmbeddingService."""
        self._client = MistralEmbeddingClient(config)
        self._dim = config.embed_dimensions

    @property
    def vector_dimension(self) -> int:
        """Vector length from config (must match Qdrant/PG pgvector columns)."""
        return self._dim

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string into a vector (length ``embed_dimensions``).

        Args:
            text: The input string to embed.

        Returns:
            A list of floats of length ``embed_dimensions``.
        """
        if not text.strip():
            logger.warning("Empty string passed to embed; returning zero vector.")
            return [0.0] * self._dim
        return await self._client.embed(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of text strings.

        Args:
            texts: A list of input strings to embed.

        Returns:
            A list of float lists, each of length ``embed_dimensions``.
        """
        if not texts:
            return []
        return await self._client.embed_batch(texts)
