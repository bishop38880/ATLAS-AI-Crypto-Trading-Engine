"""Pytest hooks for atlas/ml.

pytest-randomly can pass ``randomly_seed + crc32_offset`` to third-party
``pytest_randomly.random_seeder`` entry points without ``% 2**32``,
which breaks thinc/numpy for some nodeids. CQR tests are deterministic;
skip per-test reseed for that module only.
"""

from __future__ import annotations

from typing import Any, Generator

import pytest


def _is_cqr_item(item: pytest.Item) -> bool:
    return item.nodeid.startswith("atlas/ml/test_cqr.py::")


def _randomly_guard(item: pytest.Item) -> Generator[None, Any, None]:
    if not _is_cqr_item(item):
        yield
        return
    if not hasattr(item.config.option, "randomly_reset_seed"):
        yield
        return
    prev = item.config.option.randomly_reset_seed
    item.config.option.randomly_reset_seed = False
    yield
    item.config.option.randomly_reset_seed = prev


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _randomly_guard(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _randomly_guard(item)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item: pytest.Item) -> Generator[None, Any, None]:
    yield from _randomly_guard(item)
