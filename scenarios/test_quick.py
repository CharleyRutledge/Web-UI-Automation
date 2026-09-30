"""Tiny suites for CLI behaviour tests (fast, no browser)."""

import os

import pytest


def test_quick_pass() -> None:
    assert True


@pytest.mark.skipif(os.environ.get("SCENARIO_FAIL") != "1", reason="only when SCENARIO_FAIL=1")
def test_quick_fail() -> None:
    assert 1 + 1 == 3, "arithmetic is broken"


@pytest.mark.skipif(os.environ.get("SCENARIO_SLEEP") is None, reason="only when SCENARIO_SLEEP is set")
def test_quick_sleep() -> None:
    import time

    time.sleep(float(os.environ["SCENARIO_SLEEP"]))
