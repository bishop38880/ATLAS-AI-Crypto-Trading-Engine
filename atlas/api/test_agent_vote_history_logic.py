"""Unit tests for agent vote / calibration helpers."""

from __future__ import annotations

from atlas.api.agent_vote_history_logic import (
    find_agent_verdict_row,
    infer_vote_correctness,
    normalize_agent_match_key,
    trade_direction_from_decision,
)


def test_normalize_agent_match_key_strips_non_alnum() -> None:
    assert normalize_agent_match_key("WhaleWatcherAgent") == "whalewatcheragent"


def test_find_agent_verdict_row_matches_ws_whale_to_onchain_name() -> None:
    verdicts = [
        {"agent_name": "derivatives", "direction": "bearish"},
        {"agent_name": "onchain", "direction": "bullish", "score": 10},
    ]
    hit = find_agent_verdict_row(verdicts, "WhaleWatcherAgent")
    assert hit is not None
    assert hit["direction"] == "bullish"


def test_find_agent_verdict_row_matches_explicit_ws_token() -> None:
    verdicts = [{"agent_name": "WhaleWatcherAgent", "direction": "neutral"}]
    hit = find_agent_verdict_row(verdicts, "WhaleWatcherAgent")
    assert hit is not None


def test_infer_vote_correctness_win_long_bullish() -> None:
    ok = infer_vote_correctness(
        trade_dir="bullish",
        agent_dir="bullish",
        outcome_label="WIN",
    )
    assert ok is True


def test_infer_vote_correctness_win_long_bearish() -> None:
    ok = infer_vote_correctness(
        trade_dir="bullish",
        agent_dir="bearish",
        outcome_label="WIN",
    )
    assert ok is False


def test_infer_vote_correctness_loss_long_bullish() -> None:
    ok = infer_vote_correctness(
        trade_dir="bullish",
        agent_dir="bullish",
        outcome_label="LOSS",
    )
    assert ok is False


def test_infer_vote_correctness_loss_long_bearish_contra_right() -> None:
    ok = infer_vote_correctness(
        trade_dir="bullish",
        agent_dir="bearish",
        outcome_label="LOSS",
    )
    assert ok is True


def test_infer_vote_correctness_neutral_vote_returns_none() -> None:
    assert (
        infer_vote_correctness(trade_dir="bullish", agent_dir="neutral", outcome_label="WIN")
        is None
    )


def test_infer_vote_correctness_pending_outcome() -> None:
    assert (
        infer_vote_correctness(trade_dir="bullish", agent_dir="bullish", outcome_label=None)
        is None
    )


def test_trade_direction_from_decision_maps_strings() -> None:
    assert trade_direction_from_decision("Strong Buy") == "bullish"
    assert trade_direction_from_decision("SHORT") == "bearish"
    assert trade_direction_from_decision("Hold") is None
