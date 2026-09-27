"""Models / Overview / Settings tables: unpriced models are never shown as $0, names are escaped."""

import json

import pytest

pytestmark = pytest.mark.browser

HOSTILE = '<img src=x onerror="window.__xss=1">'


def _row(model, **extra):
    return {"model": model, "project_name": model, "event_count": 3, "prompt_tokens": 10, "completion_tokens": 5,
            "cache_read_tokens": 0, "cache_creation_tokens": 0, "total_tokens": 15, "avg_process_time_ms": 100,
            "total_cost_usd": 0.0, "estimated_cost_usd": 0.0, "unpriced_event_count": 3, "cost_per_1k_tokens": 0.0,
            **extra}


def _stub(page, path, body):
    page.route(f"**{path}*", lambda route: route.fulfill(status=200, content_type="application/json",
                                                         body=json.dumps(body)))


def test_unpriced_model_is_never_shown_as_zero(page):
    page.goto(page.base + "/")
    page.click('[data-view="models"]')
    row = page.locator("#table-models tbody tr", has_text="demo-unpriced-model")
    row.wait_for()
    assert "unpriced" in row.inner_text() and "$0.0000" not in row.inner_text()


def test_hostile_names_render_as_text(page):
    _stub(page, "/api/analytics/by-model", [_row(HOSTILE)])
    _stub(page, "/api/analytics/by-project", [_row(HOSTILE, total_cost_usd=1.5, unpriced_event_count=1)])
    _stub(page, "/api/settings/pricing", [{"model": HOSTILE + "'", "input_price_per_1m": 1, "output_price_per_1m": 2,
                                           "updated_at": "2026-09-27T00:00:00Z"}])
    page.goto(page.base + "/")
    page.wait_for_function("document.querySelectorAll('#table-projects tbody tr').length > 0")
    mixed = page.locator("#table-projects tbody tr").first.inner_text()
    assert "$1.5000" in mixed and "+1 unpriced" in mixed
    for view, table in (("models", "#table-models"), ("settings", "#table-pricing")):
        page.click(f'[data-view="{view}"]')
        page.wait_for_function(f"document.querySelectorAll('{table} tbody tr').length > 0")
        assert HOSTILE in page.inner_text(f"{table} tbody")
    assert page.evaluate("window.__xss") is None
    assert page.get_attribute("#table-pricing tbody tr", "data-model") == HOSTILE + "'"
