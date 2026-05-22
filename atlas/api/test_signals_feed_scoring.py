"""Scoring extraction for REST signal feed/detail (dict blobs from Redis)."""

from atlas.api.routes.signals import _normalized_score_int, _raw_confluence_score


def test_raw_confluence_derives_from_score_when_raw_default_zero() -> None:
    """Published JSON often includes ``raw_confluence_score: 0`` with real ``score`` ≤ 100."""
    data = {"raw_confluence_score": 0, "score": 50}
    assert _raw_confluence_score(data) == 110


def test_raw_confluence_respects_positive_raw() -> None:
    data = {"raw_confluence_score": 165, "score": 75}
    assert _raw_confluence_score(data) == 165


def test_raw_confluence_accepts_camel_raw() -> None:
    data = {"rawConfluenceScore": 140, "score": 60}
    assert _raw_confluence_score(data) == 140


def test_normalized_prefers_score_then_raw_derived() -> None:
    raw_220 = _raw_confluence_score({"raw_confluence_score": 0, "score": 40})
    assert raw_220 == 88
    assert _normalized_score_int({"score": 40}, raw_220) == 40


def test_normalized_falls_back_when_score_missing() -> None:
    raw_220 = 110
    assert _normalized_score_int({"normalized_score": 50}, raw_220) == 50


def test_normalized_derives_from_raw_only_when_no_normalized_fields() -> None:
    assert _normalized_score_int({}, 132) == min(100, int(round(132 / 220 * 100)))


def test_signal_detail_category_caps_sum_to_raw_budget() -> None:
    from atlas.api.routes.signals import _signal_detail_category_max_scores

    caps = _signal_detail_category_max_scores()
    assert len(caps) == 5
    assert sum(caps.values()) == 220.0
    assert caps["technical"] == 15.0
    assert caps["sentiment"] == 35.0
    assert caps["derivatives"] == 75.0
    assert caps["onchain"] == 65.0
    assert caps["marketContext"] == 30.0


def test_pillars_from_flat_category_scores_matches_dashboard_rollups() -> None:
    from atlas.api.routes.signals import _pillars_from_flat_category_scores

    flat = {
        "derivatives": 10.0,
        "funding": 5.0,
        "liquidation": 2.0,
        "onchain": 3.0,
        "whale": 7.0,
        "technical": 6.0,
        "sentiment": 4.0,
        "regime": 2.0,
        "macro": 1.0,
        "news_macro": 0.5,
        "correlation": 0.25,
    }
    p = _pillars_from_flat_category_scores(flat)
    assert p["derivatives"] == 17.0
    assert p["onchain"] == 10.0
    assert p["technical"] == 6.0
    assert p["sentiment"] == 4.0
    assert abs(p["market_context"] - 3.75) < 1e-6


def test_category_scores_from_metadata_requires_category_scores_key() -> None:
    from atlas.api.routes.signals import _category_scores_from_signal_history_metadata

    assert _category_scores_from_signal_history_metadata({}) is None
    assert _category_scores_from_signal_history_metadata({"category_scores": {"derivatives": 1}}) == {
        "derivatives": 1.0,
    }
