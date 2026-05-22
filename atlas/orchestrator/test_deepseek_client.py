"""Tests for DeepSeekOrchestratorClient."""

from decimal import Decimal

import asyncio
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import msgspec
import pytest


from atlas.models.enums import CrossCorrelationGrade
from atlas.models.signal import DeepSeekDecision, SignalDecision
from atlas.orchestrator.deepseek_client import DeepSeekOrchestratorClient, deepseek_breaker
from atlas.shared.config import PolarisSettings


@pytest.fixture(autouse=True)
def mock_env(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-mocked-key")


@pytest.fixture
def settings() -> PolarisSettings:
    """Provide valid PolarisSettings with mocked key."""
    return PolarisSettings()


@pytest.fixture(autouse=True)
def reset_breaker() -> None:
    """Reset the singleton circuit breaker before each test."""
    deepseek_breaker.close()


def make_mock_response(status_code: int, content: bytes) -> httpx.Response:
    request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
    return httpx.Response(status_code, content=content, request=request)


@pytest.mark.asyncio
async def test_valid_response(settings: PolarisSettings) -> None:
    """Test valid response is correctly parsed into a DeepSeekDecision."""
    client = DeepSeekOrchestratorClient(settings)

    mock_payload = {
        "id": "chatcmpl-123",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": msgspec.json.encode(
                        {
                            "decision": "Buy",
                            "confidence": 0.85,
                            "cross_correlation_grade": "ELEVATED",
                            "key_convergences": [],
                            "key_risks": [],
                            "reasoning": "Strong alignment.",
                            "would_change_if": "none",
                        }
                    ).decode("utf-8"),
                }
            }
        ],
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = make_mock_response(200, msgspec.json.encode(mock_payload))

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.BUY
        assert result.confidence == Decimal("0.85")
        assert result.cross_correlation_grade == CrossCorrelationGrade.ELEVATED
        assert result.reasoning == "Strong alignment."

    await client.close()


@pytest.mark.asyncio
async def test_http_500_fallback(settings: PolarisSettings) -> None:
    """Test HTTP 500 returns safe fallback instead of raising."""
    client = DeepSeekOrchestratorClient(settings)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_response = make_mock_response(500, b"Internal Server Error")
        mock_response.request = httpx.Request("POST", "https://api.deepseek.com/v1/chat/completions")
        mock_post.return_value = mock_response

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert result.confidence == 0.0
        assert result.cross_correlation_grade == CrossCorrelationGrade.STANDARD
        assert "HTTPStatusError" in result.reasoning

    await client.close()


@pytest.mark.asyncio
async def test_timeout_fallback(settings: PolarisSettings) -> None:
    """Test timeout returns safe fallback."""
    client = DeepSeekOrchestratorClient(settings)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Timeout")

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert result.confidence == 0.0
        assert "TimeoutException" in result.reasoning

    await client.close()


@pytest.mark.asyncio
async def test_malformed_json_fallback(settings: PolarisSettings) -> None:
    """Test malformed JSON response returns safe fallback."""
    client = DeepSeekOrchestratorClient(settings)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_payload = {"choices": [{"message": {"content": "{bad json"}}]}
        mock_post.return_value = make_mock_response(200, msgspec.json.encode(mock_payload))

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert "DecodeError" in result.reasoning

    await client.close()


@pytest.mark.asyncio
async def test_missing_required_field_fallback(settings: PolarisSettings) -> None:
    """Test missing required field in JSON response returns safe fallback."""
    client = DeepSeekOrchestratorClient(settings)

    mock_payload = {
        "choices": [
            {
                "message": {
                    "content": '{"confidence": 0.85, "cross_correlation_grade": "ELEVATED", "reasoning": "Missing decision."}'
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = make_mock_response(200, msgspec.json.encode(mock_payload))

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert "ValidationError" in result.reasoning

    await client.close()


@pytest.mark.asyncio
async def test_cross_correlation_grade_string_parsing(settings: PolarisSettings) -> None:
    """Test string values parse correctly to CrossCorrelationGrade enum."""
    client = DeepSeekOrchestratorClient(settings)

    mock_payload = {
        "choices": [
            {
                "message": {
                    "content": msgspec.json.encode(
                        {
                            "decision": "Hold",
                            "confidence": 0.7,
                            "cross_correlation_grade": "STANDARD",
                            "key_convergences": [],
                            "key_risks": [],
                            "reasoning": "...",
                            "would_change_if": "none",
                        }
                    ).decode("utf-8")
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = make_mock_response(200, msgspec.json.encode(mock_payload))

        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.cross_correlation_grade == CrossCorrelationGrade.STANDARD

    await client.close()


@pytest.mark.asyncio
async def test_client_never_raises(settings: PolarisSettings) -> None:
    """Test client returns fallback on unhandled exceptions."""
    client = DeepSeekOrchestratorClient(settings)

    with patch.object(
        client, "_build_payload", side_effect=RuntimeError("Unexpected error")
    ):
        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert "RuntimeError" in result.reasoning

    await client.close()


@pytest.mark.asyncio
async def test_circuit_breaker_open_skips_http(settings: PolarisSettings) -> None:
    """Test OPEN circuit breaker skips HTTP call and returns fallback."""
    client = DeepSeekOrchestratorClient(settings)

    deepseek_breaker.open()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        result = await client.evaluate_signal("test matrix")

        assert isinstance(result, DeepSeekDecision)
        assert result.decision == SignalDecision.HOLD
        assert "CircuitBreakerError" in result.reasoning
        mock_post.assert_not_called()

    await client.close()


@pytest.mark.asyncio
async def test_semaphore_limits_concurrency(settings: PolarisSettings) -> None:
    """Test that semaphore correctly limits concurrent connections."""
    client = DeepSeekOrchestratorClient(settings)

    active_requests = 0
    max_active_observed = 0

    async def mock_post_impl(*args: Any, **kwargs: Any) -> httpx.Response:
        nonlocal active_requests, max_active_observed
        active_requests += 1
        max_active_observed = max(max_active_observed, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        mock_payload = {
            "choices": [
                {
                    "message": {
                        "content": msgspec.json.encode(
                            {
                                "decision": "Hold",
                                "confidence": 0.1,
                                "cross_correlation_grade": "STANDARD",
                                "key_convergences": [],
                                "key_risks": [],
                                "reasoning": "...",
                                "would_change_if": "...",
                            }
                        ).decode("utf-8")
                    }
                }
            ]
        }
        return make_mock_response(200, msgspec.json.encode(mock_payload))

    with patch("httpx.AsyncClient.post", side_effect=mock_post_impl):
        tasks = [client.evaluate_signal(f"matrix {i}") for i in range(15)]
        await asyncio.gather(*tasks)

    assert max_active_observed <= settings.deepseek_max_concurrent
    await client.close()


def test_url_literal_is_exact() -> None:
    """Verify deepseek_client.py contains exact URL literal without markdown wrappers."""
    client_path = Path(__file__).parent / "deepseek_client.py"
    content = client_path.read_text()

    assert '"https://api.deepseek.com/v1/chat/completions"' in content

    # Ensure no markdown links like [link](url)
    assert not re.search(r"\[.*?\]\(https://api\.deepseek\.com.*?\)", content)
    # Ensure no markdown raw links like <https://api.deepseek.com>
    assert not re.search(r"<https://api\.deepseek\.com.*?>", content)


def test_logger_format() -> None:
    """Verify loguru usage strictly follows positional `{}` and avoids f-strings or trailing kwargs."""
    client_path = Path(__file__).parent / "deepseek_client.py"
    content = client_path.read_text()

    for i, line in enumerate(content.splitlines()):
        if "logger." in line:
            assert "f\"" not in line and "f'" not in line, f"Line {i+1} uses f-string for logger: {line}"
            # Naive check for trailing kwargs logger.error("msg", k=v)
            assert not re.search(r'logger\.(info|error|warning|debug|critical)\([^,"]*,\s*\w+=', line), f"Line {i+1} uses trailing kwargs for logger: {line}"
