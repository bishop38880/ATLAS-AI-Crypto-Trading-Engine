"""Unit tests for decision_journal_logic helpers."""

from __future__ import annotations

from atlas.api.decision_journal_logic import (
    build_score_breakdown_label,
    outcome_horizons_from_metadata,
    resolve_primary_agent,
)


def test_resolve_primary_agent_picks_veto() -> None:
    verdicts = [
        {"agent_name": "technical", "score": 40, "veto": False},
        {"agent_name": "risk", "score": 0, "veto": True},
    ]
    assert resolve_primary_agent(verdicts, {}) == "risk"


def test_resolve_primary_agent_highest_score() -> None:
    verdicts = [
        {"agent_name": "a", "score": 10, "veto": False},
        {"agent_name": "b", "score": 90, "veto": False},
    ]
    assert resolve_primary_agent(verdicts, {}) == "b"


def test_outcome_horizons_fills_from_pnl() -> None:
    h1, h4, h24 = outcome_horizons_from_metadata({}, 1.25)
    assert h1 == 1.25
    assert h4 == 1.25
    assert h24 == 1.25


def test_outcome_horizons_reads_metadata_keys() -> None:
    meta = {"outcome_pct_1h": 0.5, "outcome_pct_4h": -0.2, "outcome_pct_24h": 1.0}
    h1, h4, h24 = outcome_horizons_from_metadata(meta, None)
    assert h1 == 0.5
    assert h4 == -0.2
    assert h24 == 1.0


def test_build_score_breakdown_label() -> None:
    s = build_score_breakdown_label(72, 158, 0.81)
    assert "72/100" in s
    assert "158/220" in s
    assert "0.81" in s
