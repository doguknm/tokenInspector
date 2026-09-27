"""Complexity view in a real browser: tier x model matrix, filters, stale-response discard."""

import pytest

from .test_tasks_view import DELAY_FETCH

pytestmark = pytest.mark.browser


def open_complexity(page):
    page.goto(page.base + "/")
    page.click('[data-view="complexity"]')
    page.wait_for_function("document.querySelectorAll('#table-cx-matrix tbody tr').length > 0")


def _models(page):
    return set(page.locator("#table-cx-matrix tbody td:nth-child(2)").all_inner_texts())


def test_matrix_shows_models_per_tier_with_low_sample_and_unpriced(page):
    open_complexity(page)
    assert "request-shape-v1" in page.inner_text("#cx-note")
    assert page.locator("#table-cx-tiers tbody tr").count() == 5
    assert _models(page) == {"claude-haiku-4-5", "claude-sonnet-4-6", "demo-unpriced-model"}
    unpriced = page.locator("#table-cx-matrix tbody tr", has_text="demo-unpriced-model")
    assert "low sample" in unpriced.inner_text() and "unpriced" in unpriced.inner_text()
    assert "$0.0000" not in unpriced.inner_text()
    assert page.locator("#table-cx-matrix .best").count() > 0
    assert page.evaluate("charts['complexity'].data.datasets.length") == 3


def test_model_filter_and_latest_response_wins(page):
    open_complexity(page)
    page.evaluate(DELAY_FETCH, [r"complexity-matrix.*model=claude-sonnet", 1200, 0])
    page.select_option("#cx-model", "claude-sonnet-4-6")
    page.select_option("#cx-model", "claude-haiku-4-5")
    page.wait_for_timeout(1800)
    assert _models(page) == {"claude-haiku-4-5"}
    assert page.input_value("#cx-model") == "claude-haiku-4-5"
    options = page.locator("#cx-model option").all_inner_texts()
    assert options == ["All models", "claude-haiku-4-5", "claude-sonnet-4-6", "demo-unpriced-model"]
