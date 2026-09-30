"""A large generated suite for load/performance checks of the reporting pipeline."""

import os

import pytest

COUNT = int(os.environ.get("SCENARIO_LOAD_TESTS", "0"))


@pytest.mark.parametrize("n", range(COUNT))
def test_generated(n: int) -> None:
    assert n % 50 != 49, f"generated failure #{n}"
