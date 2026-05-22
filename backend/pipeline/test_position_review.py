"""Position review staged-timeout tests (mocked LLM — no network)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import httpx
import pytest

from atlas.shared.config import PolarisSettings
from backend.pipeline.gate_score import Direction, GateChainResult
from backend.pipeline.position_review import FillContext, PositionReviewer


class _MockRiskGovernor:
    def __init__(self) -> None:
        self.partial_calls: list[dict[str, object]] = []
        self.full_calls: list[dict[str, object]] = []

    async def partial_close(self, **kwargs: object) -> None:
        self.partial_calls.append(kwargs)

    async def full_close(self, **kwargs: object) -> None:
        self.full_calls.append(kwargs)


@pytest.mark.asyncio
async def test_review_timeout_triggers_partial_then_full_close() -> None:
    risk = _MockRiskGovernor()
    settings = PolarisSettings(DEEPSEEK_API_KEY="", MISTRAL_API_KEY="")
    http = httpx.AsyncClient()

    reviewer = PositionReviewer(
        risk,
        settings=settings,
        http_client=http,
        partial_exit_at_s=0.15,
        full_exit_at_s=0.35,
    )

    fill = FillContext(
        symbol="BTC",
        direction="LONG",
        entry_price=67000.0,
        position_size_usd=150.0,
        leverage=3,
        fill_timestamp=time.time(),
        gate_chain=GateChainResult(
            symbol="BTC",
            passed=True,
            final_score=155,
            final_direction=Direction.LONG,
        ),
        order_id="test_order_001",
    )

    async def _hang_forever(*_args: object, **_kwargs: object) -> dict[str, str]:
        await asyncio.sleep(60.0)
        return {"verdict": "HOLD"}

    with patch.object(reviewer, "_request_review", side_effect=_hang_forever):
        verdict = await reviewer.review(fill)

    await http.aclose()

    assert verdict.verdict == "TIMEOUT"
    assert verdict.model == "timeout"
    assert verdict.position_remaining_pct == 0.0
    assert len(risk.partial_calls) == 1
    assert risk.partial_calls[0]["close_pct"] == 0.5
    assert len(risk.full_calls) == 1
