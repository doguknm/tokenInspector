from __future__ import annotations

import pytest

from cc_support import modules


@pytest.fixture
def complexity():
    return modules()["cc_complexity"]


@pytest.mark.parametrize(("total", "tier"), [
    (0, 1), (3999, 1), (4000, 2), (4001, 2), (15999, 2), (16000, 3), (16001, 3),
    (63999, 3), (64000, 4), (64001, 4), (127999, 4), (128000, 5), (128001, 5),
])
def test_input_size_boundaries(complexity, total, tier):
    assert complexity.input_size_tier(total, 0, 0) == tier


def test_cache_is_included(complexity):
    assert complexity.input_size_tier(1, 3999, 12000) == 3


@pytest.mark.parametrize("values", [(None, 0, 0), (-1, 0, 0), (True, 0, 0), (0, "0", 0), (0, 0, 1.0)])
def test_malformed_usage_is_unscored(complexity, values):
    assert complexity.input_size_tier(*values) is None
