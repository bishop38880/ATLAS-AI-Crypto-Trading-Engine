"""Tests for Embedding Client."""

import httpx
import pytest
from pydantic import SecretStr

from atlas.core.embedding_client import EmbeddingProviderError, MistralEmbeddingClient
from atlas.shared.config import ModelStackConfig, PolarisSettings


@pytest.fixture
def mock_config():
    return ModelStackConfig(
        _env_file=None,
        embed_provider="mistral",
        embed_model="mistral-embed",
        MISTRAL_API_KEY=SecretStr("test-mistral-secret"),
        embed_batch_size=32
    )


@pytest.mark.asyncio
async def test_mistral_embed_returns_1024_dim(mock_config, respx_mock):
    client = MistralEmbeddingClient(mock_config)
    
    mock_vector = [0.1] * 1024
    
    respx_mock.post("https://api.mistral.ai/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": mock_vector}
                ]
            }
        )
    )

    vec = await client.embed("test text")
    assert len(vec) == 1024
    assert vec == mock_vector


@pytest.mark.asyncio
async def test_mistral_batch_splitting(mock_config, respx_mock):
    mock_config.embed_batch_size = 32
    client = MistralEmbeddingClient(mock_config)
    
    # 100 texts -> 4 calls (32, 32, 32, 4)
    texts = [f"Text {i}" for i in range(100)]
    
    def handle_request(request: httpx.Request):
        import msgspec
        body = msgspec.json.decode(request.read())
        inputs = body["input"]
        return httpx.Response(
            200, 
            json={"data": [{"index": i, "embedding": [0.1] * 1024} for i in range(len(inputs))]}
        )
        
    route = respx_mock.post("https://api.mistral.ai/v1/embeddings").mock(side_effect=handle_request)

    results = await client.embed_batch(texts)
    
    assert len(results) == 100
    assert len(results[0]) == 1024
    assert len(route.calls) == 4


@pytest.mark.asyncio
async def test_lmstudio_embed_hits_local_embeddings_endpoint(respx_mock) -> None:
    cfg = ModelStackConfig(
        _env_file=None,
        embed_provider="lmstudio",
        lmstudio_base_url="http://127.0.0.1:1234/v1",
        embed_model="nomic-embed",
        MISTRAL_API_KEY=SecretStr(""),
    )
    client = MistralEmbeddingClient(cfg)
    respx_mock.post("http://127.0.0.1:1234/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1] * 1024}]},
        ),
    )
    vec = await client.embed("test")
    assert len(vec) == 1024


def test_lmstudio_bringup_env_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMBED_PROVIDER", "lmstudio")
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://192.168.5.26:1234/v1")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-qwen3-embedding-0.6b")
    monkeypatch.setenv("EMBED_DIMENSIONS", "1024")
    monkeypatch.setenv(
        "ROUTER_API_ENDPOINT",
        "http://192.168.5.26:1234/v1/chat/completions",
    )
    monkeypatch.setenv("ROUTER_API_MODEL", "mistralai/mistral-3-14b-reasoning")
    monkeypatch.setenv("ROUTER_LOCAL_MODEL", "mistralai/mistral-3-14b-reasoning")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "lm-studio")

    settings = PolarisSettings(_env_file=None)

    assert settings.embed_provider == "lmstudio"
    assert settings.lmstudio_base_url == "http://192.168.5.26:1234/v1"
    assert settings.embed_model == "text-embedding-qwen3-embedding-0.6b"
    assert settings.embed_dimensions == 1024
    assert settings.router_api_endpoint.endswith("/v1/chat/completions")
    assert settings.router_api_model == "mistralai/mistral-3-14b-reasoning"


@pytest.mark.asyncio
async def test_embedding_wrong_dimension_raises(mock_config, respx_mock) -> None:
    client = MistralEmbeddingClient(mock_config)
    respx_mock.post("https://api.mistral.ai/v1/embeddings").mock(
        return_value=httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.1] * 768}]},
        ),
    )
    with pytest.raises(EmbeddingProviderError) as exc:
        await client.embed("test")
    assert "dimension mismatch" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_mistral_missing_key_raises(mock_config, respx_mock) -> None:
    mock_config = ModelStackConfig(
        _env_file=None,
        embed_provider="mistral",
        MISTRAL_API_KEY=SecretStr(""),
        embed_batch_size=32,
    )
    client = MistralEmbeddingClient(mock_config)
    with pytest.raises(EmbeddingProviderError):
        await client.embed("test")


@pytest.mark.asyncio
async def test_mistral_failure_raises_provider_error(mock_config, respx_mock):
    client = MistralEmbeddingClient(mock_config)
    
    respx_mock.post("https://api.mistral.ai/v1/embeddings").mock(
        return_value=httpx.Response(
            500,
            text="Internal Server Error"
        )
    )

    with pytest.raises(EmbeddingProviderError) as exc_info:
        await client.embed("test")
        
    assert exc_info.value.retriable is False
    assert "500" in str(exc_info.value)
