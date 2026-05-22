"""Unit tests for polaris signal Redis key alias expansion."""

from atlas.api.polaris_signals_redis_keys import (
    polaris_signal_redis_keys,
    polaris_signal_wire_tokens,
    signal_latest_redis_keys,
)


def test_keys_for_base_includes_usdt_aliases() -> None:
    keys = polaris_signal_redis_keys("BTC")
    assert keys == [
        "polaris:signals:BTC",
        "polaris:signals:BTC/USDT",
        "polaris:signals:BTCUSDT",
    ]


def test_keys_for_usdt_pair_includes_slash_form() -> None:
    keys = polaris_signal_redis_keys("BTCUSDT")
    assert keys == [
        "polaris:signals:BTCUSDT",
        "polaris:signals:BTC",
        "polaris:signals:BTC/USDT",
    ]


def test_keys_for_slash_pair_includes_bare_and_concat() -> None:
    keys = polaris_signal_redis_keys("BTC/USDT")
    assert keys == [
        "polaris:signals:BTC/USDT",
        "polaris:signals:BTC",
        "polaris:signals:BTCUSDT",
    ]


def test_strips_whitespace() -> None:
    keys = polaris_signal_redis_keys("  btc  ")
    assert keys[0] == "polaris:signals:BTC"


def test_signal_latest_keys_follow_polaris_structure() -> None:
    keys = signal_latest_redis_keys("BTC")
    assert keys == [
        "signal:latest:BTC",
        "signal:latest:BTC/USDT",
        "signal:latest:BTCUSDT",
    ]


def test_rndr_includes_render_rebrand_aliases() -> None:
    tokens = polaris_signal_wire_tokens("RNDR")
    assert "RNDRUSDT" in tokens
    assert "RENDERUSDT" in tokens
    assert "RENDER/USDT" in tokens
