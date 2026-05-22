"""Tests for LLM Clients."""

import httpx
import msgspec
import pytest
from pydantic import SecretStr

from atlas.core.llm_client import (
    DeepSeekClient,
    LLMProviderError,
    LocalLLMClient,
    LocalLLMUnavailableError,
)
from atlas.shared.config import ModelStackConfig


@pytest.fixture
def mock_config():
    return ModelStackConfig(
        DEEPSEEK_API_KEY=SecretStr("test-deepseek-secret"),
        MISTRAL_API_KEY=SecretStr("test-mistral-secret"),
        local_enabled=False,
        deepseek_base_url="https://api.deepseek.com",
        _env_file=None,
    )


def _deepseek_completions_url(cfg: ModelStackConfig) -> str:
    return f"{cfg.deepseek_base_url.rstrip('/')}/chat/completions"


@pytest.mark.asyncio
async def test_deepseek_formats_request_correctly(mock_config, respx_mock):
    client = DeepSeekClient(mock_config, is_reasoner=False)

    route = respx_mock.post(_deepseek_completions_url(mock_config)).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "Hello World"}}],
                "usage": {"total_tokens": 10}
            }
        )
    )

    resp = await client.complete("Say hello")
    
    assert resp.text == "Hello World"
    assert resp.model == "deepseek-chat"
    
    assert route.called
    request = route.calls.last.request
    assert request is not None
    assert request.headers["authorization"] == "Bearer test-deepseek-secret"
    
    # Verify SecretStr is used correctly and not leaked in string representation
    assert str(mock_config.deepseek_api_key) == "**********"
    assert repr(mock_config.deepseek_api_key) == "SecretStr('**********')"


@pytest.mark.asyncio
async def test_deepseek_parses_reasoning_content(mock_config, respx_mock):
    client = DeepSeekClient(mock_config, is_reasoner=True)

    respx_mock.post(_deepseek_completions_url(mock_config)).mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "content": "Final answer",
                        "reasoning_content": "Step 1, Step 2"
                    }
                }],
                "usage": {"total_tokens": 20}
            }
        )
    )

    resp = await client.complete("Solve this")
    
    assert resp.text == "Final answer"
    assert resp.reasoning == "Step 1, Step 2"
    assert resp.model == "deepseek-reasoner"


@pytest.mark.asyncio
async def test_deepseek_timeout_raises_retriable_error(mock_config, respx_mock):
    client = DeepSeekClient(mock_config)

    respx_mock.post(_deepseek_completions_url(mock_config)).mock(
        side_effect=httpx.TimeoutException("Read timeout")
    )

    with pytest.raises(LLMProviderError) as exc_info:
        await client.complete("Test timeout")
        
    assert exc_info.value.retriable is True


@pytest.mark.asyncio
async def test_local_client_health_check_disabled(mock_config):
    client = LocalLLMClient(mock_config)
    
    # Config has local_enabled=False
    is_healthy = await client.health_check()
    assert is_healthy is False

    with pytest.raises(LocalLLMUnavailableError) as exc_info:
        await client.complete("Should fail")

    assert exc_info.value.retriable is False
    assert "not enabled" in str(exc_info.value)
