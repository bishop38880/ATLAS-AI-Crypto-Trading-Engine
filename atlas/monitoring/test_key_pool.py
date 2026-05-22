"""Tests for sequential API key failover pool."""

from __future__ import annotations

import time

from atlas.monitoring.key_pool import ApiKeySlot, FailoverKeyPool


class TestFailoverKeyPool:
    def test_select_key_prefers_primary_when_available(self) -> None:
        pool = FailoverKeyPool(
            [
                ApiKeySlot(key_id="primary", secret="a"),
                ApiKeySlot(key_id="secondary", secret="b"),
            ]
        )
        for _ in range(10):
            assert pool.select_key(time.monotonic()).key_id == "primary"

    def test_cooldown_fails_over_to_secondary(self) -> None:
        pool = FailoverKeyPool(
            [
                ApiKeySlot(key_id="primary", secret="1"),
                ApiKeySlot(key_id="secondary", secret="2"),
            ]
        )
        now = time.monotonic()
        pool.mark_cooldown("primary", now + 60.0)
        assert pool.select_key(now).key_id == "secondary"
        eligible = pool.eligible_keys(now)
        assert [key.key_id for key in eligible] == ["secondary"]

    def test_primary_recovered_after_cooldown_expires(self) -> None:
        pool = FailoverKeyPool(
            [
                ApiKeySlot(key_id="primary", secret="1"),
                ApiKeySlot(key_id="secondary", secret="2"),
            ]
        )
        now = time.monotonic()
        pool.mark_cooldown("primary", now - 1.0)
        assert pool.select_key(now).key_id == "primary"

    def test_all_keys_exhausted_returns_full_list_for_hail_mary(self) -> None:
        pool = FailoverKeyPool(
            [
                ApiKeySlot(key_id="primary", secret="1"),
                ApiKeySlot(key_id="secondary", secret="2"),
            ]
        )
        now = time.monotonic()
        pool.mark_cooldown("primary", now + 60.0)
        pool.mark_cooldown("secondary", now + 60.0)
        assert len(pool.eligible_keys(now)) == 2

    def test_requires_at_least_one_key(self) -> None:
        try:
            FailoverKeyPool([])
            assert False, "expected ValueError"
        except ValueError:
            pass
