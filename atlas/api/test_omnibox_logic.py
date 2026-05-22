from __future__ import annotations

from atlas.api.omnibox_logic import (
    classify_omnibox_route,
    omnibox_backend_is_deepseek,
    omnibox_context_flags,
    resolved_omnibox_lmstudio_model_id,
)
from atlas.shared.config import PolarisSettings


def test_route_override_wins() -> None:
    route, why = classify_omnibox_route("hello", "MCP_ONLY")
    assert route == "MCP_ONLY"
    assert "locked" in why


def test_rag_keywords_only() -> None:
    route, _ = classify_omnibox_route("Show similar historical signals", None)
    assert route == "RAG_ONLY"


def test_mcp_keywords_only() -> None:
    route, _ = classify_omnibox_route("What is the funding rate right now", None)
    assert route == "MCP_ONLY"


def test_hybrid_default() -> None:
    route, _ = classify_omnibox_route("What should I watch today", None)
    assert route == "HYBRID"


def test_context_flags_direct() -> None:
    assert omnibox_context_flags("DIRECT") == (False, False)


def test_resolved_model_prefers_explicit_omnibox_field() -> None:
    s = PolarisSettings(
        omnibox_chat_model="custom-local",
        router_local_model="router-local-model",
        _env_file=None,
    )
    assert resolved_omnibox_lmstudio_model_id(s) == "custom-local"


def test_resolved_model_falls_through_router_locals() -> None:
    s = PolarisSettings(
        omnibox_chat_model="",
        router_local_model="mistralai/mistral-3-14b-reasoning",
        router_api_model="fallback-api",
        _env_file=None,
    )
    assert resolved_omnibox_lmstudio_model_id(s) == "mistralai/mistral-3-14b-reasoning"


def test_backend_deepseek_detection() -> None:
    lm = PolarisSettings(omnibox_chat_backend="lmstudio", _env_file=None)
    ds = PolarisSettings(omnibox_chat_backend="deepseek", _env_file=None)
    assert omnibox_backend_is_deepseek(lm) is False
    assert omnibox_backend_is_deepseek(ds) is True
