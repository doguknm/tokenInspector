"""Tasks view in a real browser (AC7g-AC7p) with stubbed responses where a state must be forced."""

import json
import re

import pytest

from .conftest import open_tasks

pytestmark = pytest.mark.browser


def _item(i, total_ts="2026-09-27T10:00:00.000000Z"):
    return {"task_ref": f"{i:032x}", "project_name": "stub", "session_id": "s", "turn_id": f"stub-{i:03d}",
            "source_task_id": None, "parent_task_ref": None, "root_task_ref": None, "hierarchy_status": "root",
            "child_count": 0, "first_seen_at": total_ts, "last_seen_at": total_ts, "wall_time_ms": 1,
            "completion": "open", "completed_at": None, "llm_request_count": 1, "tool_call_count": 0,
            "error_count": 0, "retry_count": 0, "prompt_tokens": 1, "completion_tokens": 1, "cache_read_tokens": 0,
            "cache_creation_tokens": 0, "total_tokens": 2, "cost_usd": None, "estimated_cost_usd": 0.0,
            "unpriced_count": 0, "start_complexity": 3, "start_complexity_method": "request-shape-v1",
            "prompt_state": "none", "has_prompt": False, "prompt_purged": False, "prompt_expires_at": None,
            "jev": None, "human_label_count": 0}


def _list(total, page, size=50):
    start = (page - 1) * size
    items = [_item(i) for i in range(start, min(start + size, total))]
    return json.dumps({"items": items, "total": total, "page": page, "page_size": size, "projects": ["stub"]})


def _fulfill_json(body):
    return lambda route: route.fulfill(json=body)


# Delays are injected at the fetch level: the sync Playwright API cannot hold a route open.
DELAY_FETCH = """([pattern, ms, status]) => {
  const real = window.__realFetch || (window.__realFetch = window.fetch.bind(window));
  window.fetch = (url, opts) => new RegExp(pattern).test(String(url))
    ? new Promise(r => setTimeout(r, ms)).then(() => status ? new Response('{"detail":"late"}', {status}) : real(url, opts))
    : real(url, opts);
}"""


def _visible(page, selector):
    return page.eval_on_selector(selector, "e => getComputedStyle(e).display !== 'none'")


def test_real_data_is_escaped_and_never_holds_prompt_or_note_text(page):
    open_tasks(page)
    assert page.evaluate("window.__xss ?? null") is None
    html = page.content()
    assert "CANARY-PROMPT-TEXT-7731" not in html and "CANARY-NOTE-5512" not in html
    hostile = page.locator("#table-tasks td.task-id", has_text="<img").first
    assert "<img" in hostile.inner_text() and hostile.get_attribute("title").startswith('x"><img')
    hostile.click()
    page.wait_for_function("getComputedStyle(document.getElementById('task-detail')).display !== 'none'")
    assert page.evaluate("window.__xss ?? null") is None
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    page.locator("#table-tasks td.task-id", has_text="demo-task-1").first.click()
    page.wait_for_timeout(500)
    assert not any("include_prompt" in url for url in requests)
    assert "CANARY" not in page.content()


def test_status_line_states(page):
    states = [
        ({"enabled": True, "spent_today_usd": 0.0012, "budget_day_usd": 0.05, "calls_today": 3,
          "max_calls_per_day": 200, "running": False, "last_error_type": None, "last_run": None},
         "JEV: enabled — today $0.0012 of $0.05, 3/200 calls"),
        ({"enabled": True, "spent_today_usd": 0, "budget_day_usd": 0.05, "calls_today": 0, "max_calls_per_day": 200,
          "running": True, "last_error_type": None, "last_run": None}, "— running"),
        ({"enabled": True, "spent_today_usd": 0.05, "budget_day_usd": 0.05, "calls_today": 9, "max_calls_per_day": 200,
          "running": False, "last_error_type": "budget_exceeded", "last_run": None}, "budget ceiling reached"),
        ({"enabled": True, "spent_today_usd": 0, "budget_day_usd": 0.05, "calls_today": 1, "max_calls_per_day": 200,
          "running": False, "last_error_type": "deferred_rate_limited",
          "last_run": {"retry_not_before": "2026-09-27T13:00:00.000000Z"}}, "retry after 2026-09-27 13:00:00"),
        ({"enabled": False, "disabled_reason": "ingest_token_missing"}, "(config error: ingest_token_missing)"),
    ]
    for body, expected in states:
        page.unroute("**/api/tasks/evaluator-status")
        page.route("**/api/tasks/evaluator-status", _fulfill_json(body))
        open_tasks(page)
        page.wait_for_function(f"document.getElementById('tasks-jev-status').textContent.includes({json.dumps(expected)})")
    page.unroute("**/api/tasks/evaluator-status")
    page.route("**/api/tasks/evaluator-status", lambda route: route.fulfill(status=500, body="x"))
    open_tasks(page)
    page.wait_for_function("document.getElementById('tasks-jev-status').textContent === 'JEV: status unavailable'")
    assert page.locator("#table-tasks tbody tr").count() > 0


def test_detail_not_found_and_error(page):
    open_tasks(page)
    page.route(re.compile(r".*/api/tasks/[0-9a-f]{32}$"), lambda route: route.fulfill(status=404, json={"detail": "x"}))
    page.locator("#table-tasks tbody tr").first.click()
    page.wait_for_function("document.getElementById('task-detail-body').textContent === 'Task not found.'")
    page.unroute(re.compile(r".*/api/tasks/[0-9a-f]{32}$"))
    page.route(re.compile(r".*/api/tasks/[0-9a-f]{32}$"), lambda route: route.fulfill(status=500, body="broken"))
    page.locator("#table-tasks tbody tr").nth(1).click()
    page.wait_for_function("document.getElementById('task-detail-body').textContent.startsWith('Could not load task detail.')")


def test_list_failure_clears_rows(page):
    open_tasks(page)
    page.route(re.compile(r".*/api/tasks\?.*page_size=50.*"), lambda route: route.fulfill(status=500, body="down"))
    page.click("#tasks-days-7")
    page.wait_for_function("getComputedStyle(document.getElementById('tasks-error')).display !== 'none'")
    assert page.locator("#table-tasks tbody tr").count() == 0


def test_real_out_of_range_reloads_last_page_exactly_once(page):
    state = {"total": 200}
    log = []

    def handler(route):
        page_no = int(re.search(r"page=(\d+)", route.request.url).group(1))
        log.append(page_no)
        route.fulfill(body=_list(state["total"], page_no), content_type="application/json")

    page.route(re.compile(r".*/api/tasks\?.*page_size=50.*"), handler)
    open_tasks(page)
    for _ in range(3):
        page.click("#tasks-next")
        page.wait_for_timeout(150)
    page.wait_for_function("document.getElementById('tasks-page-info').textContent.startsWith('Page 4 of 4')")
    state["total"] = 120
    log.clear()
    page.click('[data-view="overview"]')
    page.click('[data-view="tasks"]')
    page.wait_for_function("document.getElementById('tasks-page-info').textContent === 'Page 3 of 3 (120 tasks)'")
    assert log == [4, 3]
    assert page.locator("#table-tasks tbody tr").count() == 20
    assert page.is_disabled("#tasks-next") and not page.is_disabled("#tasks-prev")


def test_chart_only_failure_and_recovery(page):
    open_tasks(page)
    page.wait_for_function("!!Chart.getChart(document.getElementById('chart-tasks-scatter'))")
    page.route(re.compile(r".*evaluated=true.*page_size=200.*"), lambda route: route.fulfill(status=500, body="x"))
    page.click("#tasks-days-90")
    page.wait_for_function("getComputedStyle(document.getElementById('tasks-chart-error')).display !== 'none'")
    assert page.evaluate("Chart.getChart(document.getElementById('chart-tasks-scatter')) === undefined")
    assert not _visible(page, "#chart-tasks-scatter") and not _visible(page, "#tasks-chart-cap")
    assert not _visible(page, "#tasks-error") and page.locator("#table-tasks tbody tr").count() == 50
    page.unroute(re.compile(r".*evaluated=true.*page_size=200.*"))
    page.click("#tasks-days-30")
    page.wait_for_function("getComputedStyle(document.getElementById('tasks-chart-error')).display === 'none'")


def test_chart_cap_note(page):
    def handler(route):
        items = [dict(_item(i), jev={"display_score": 2.0, "confidence": 0.5, "probabilities": [0.2] * 5})
                 for i in range(200)]
        route.fulfill(json={"items": items, "total": 207, "page": 1, "page_size": 200, "projects": ["stub"]})

    page.route(re.compile(r".*evaluated=true.*page_size=200.*"), handler)
    open_tasks(page)
    page.wait_for_function("document.getElementById('tasks-chart-cap').textContent === "
                           "'Showing latest 200 of 207 scored tasks'")


def test_close_invalidates_in_flight_detail(page):
    open_tasks(page)
    rows = page.locator("#table-tasks tbody tr")
    for status in (0, 500):
        page.evaluate("window.fetch = window.__realFetch || window.fetch")
        rows.nth(0).click()  # Close is only clickable while the panel is open
        page.wait_for_function("getComputedStyle(document.getElementById('task-detail')).display !== 'none'")
        page.evaluate(DELAY_FETCH, [r"/api/tasks/[0-9a-f]{32}$", 1500, status])
        rows.nth(2).click()
        page.click("#task-detail-close")
        page.wait_for_timeout(2000)
        assert not _visible(page, "#task-detail")
        assert "late" not in page.inner_text("#task-detail-body")
    page.evaluate("window.fetch = window.__realFetch")
    rows.nth(3).click()
    page.wait_for_function("getComputedStyle(document.getElementById('task-detail')).display !== 'none'")


def test_latest_filter_wins_and_project_options_stay(page):
    open_tasks(page)
    page.evaluate(DELAY_FETCH, [r"project=demo-beta.*page_size=50|page_size=50.*project=demo-beta", 1200, 0])
    page.select_option("#tasks-project", "demo-beta")
    page.select_option("#tasks-project", "demo-alpha")
    page.wait_for_timeout(1800)
    projects = set(page.locator("#table-tasks tbody td:nth-child(2)").all_inner_texts())
    assert projects == {"demo-alpha"}
    options = page.locator("#tasks-project option").all_inner_texts()
    assert options == ["All projects", "demo-alpha", "demo-beta"]
