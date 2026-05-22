"""Embedding Client Abstraction Layer.

Provides 1024-dimensional embeddings via Mistral.
"""

from typing import Any

import httpx
import msgspec

from atlas.shared.config import ModelStackConfig


class EmbeddingProviderError(Exception):
    """Exception raised for Embedding API failures."""
    
    def __init__(self, message: str, retriable: bool = False):
        super().__init__(message)
        self.retriable = retriable


class MistralEmbeddingClient:
    """OpenAI-compatible embeddings client (Mistral cloud or LM Studio local)."""

    def __init__(self, config: ModelStackConfig):
        self._config = config
        self._client = httpx.AsyncClient(timeout=self._config.embed_timeout_s)

    def _embedding_url(self) -> str:
        """POST target for embeddings (Mistral API or LM Studio ``/v1/embeddings``)."""
        if self._config.embed_provider.lower() == "lmstudio":
            base = self._config.lmstudio_base_url.rstrip("/")
            return f"{base}/embeddings"
        return self._config.embed_endpoint

    def _authorization_bearer(self) -> str:
        """Bearer token; LM Studio accepts any non-empty placeholder."""
        key = self._config.embed_api_key.get_secret_value()
        if key:
            return key
        if self._config.embed_provider.lower() == "lmstudio":
            return "lm-studio"
        raise EmbeddingProviderError(
            "MISTRAL_API_KEY is empty — set it for Mistral, or use EMBED_PROVIDER=lmstudio for local LM Studio.",
            retriable=False,
        )

    def _validate_dimensions(self, vectors: list[list[float]]) -> None:
        expected = self._config.embed_dimensions
        for i, vec in enumerate(vectors):
            if len(vec) != expected:
                raise EmbeddingProviderError(
                    (
                        f"Embedding dimension mismatch | row={i} | got={len(vec)} | expected={expected} | "
                        f"Qdrant/PG are configured for vector({expected}). "
                        "Use a matching embedding model in LM Studio (or Mistral), or align EMBED_DIMENSIONS "
                        "with the model and rebuild vector collections."
                    ),
                    retriable=False,
                )

    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of strings, splitting them by configured batch size."""
        if not texts:
            return []

        import asyncio
        batch_size = self._config.embed_batch_size
        coros = [
            self._embed_chunk(texts[i : i + batch_size])
            for i in range(0, len(texts), batch_size)
        ]
        chunks = await asyncio.gather(*coros)

        all_embeddings: list[list[float]] = []
        for chunk in chunks:
            all_embeddings.extend(chunk)

        return all_embeddings

    async def _embed_chunk(self, texts: list[str]) -> list[list[float]]:
        """Process a single chunk of texts."""
        headers = {
            "Authorization": f"Bearer {self._authorization_bearer()}",
            "Content-Type": "application/json",
        }

        body = {
            "model": self._config.embed_model,
            "input": texts,
        }

        url = self._embedding_url()
        provider_label = "lmstudio" if self._config.embed_provider.lower() == "lmstudio" else "mistral"

        try:
            resp = await self._client.post(
                url,
                content=msgspec.json.encode(body),
                headers=headers,
            )
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise EmbeddingProviderError(f"{provider_label} embedding timeout: {e}", retriable=True)
        except httpx.HTTPStatusError as e:
            raise EmbeddingProviderError(
                f"{provider_label} embedding HTTP {e.response.status_code}: {e.response.text[:500]}",
                retriable=False,
            )
        except Exception as e:
            raise EmbeddingProviderError(f"{provider_label} embedding connection error: {e}", retriable=True)

        data = msgspec.json.decode(resp.content)

        # Sort embeddings by their index in the response to maintain original order
        # Mistral typically returns an array of objects under 'data'
        try:
            data_items = data["data"]
            # We assume Mistral preserves order or provides 'index'
            sorted_items = sorted(data_items, key=lambda x: x.get("index", 0))
            vectors = [item["embedding"] for item in sorted_items]
        except KeyError as e:
            raise EmbeddingProviderError(f"Unexpected response format: {e}", retriable=False)

        self._validate_dimensions(vectors)
        return vectors

    async def health_check(self) -> bool:
        """Check if Mistral embeddings are available."""
        try:
            await self.embed("health")
            return True
        except Exception:
            return False
