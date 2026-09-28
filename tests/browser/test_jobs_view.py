"""Jobs view in a real browser (O9 AC1.6): cost per launcher job with the API's semantics.

Own module-scoped server with its own DB (the shared demo server is not changed); data is seeded
through the ingest API. Fabricated /api/jobs responses force the states seeding cannot produce cheaply.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from .conftest import REPO, _free_port

pytestmark = pytest.mark.browser

J111, J222, J333 = "20260928-100000-111", "20260928-110000-222", "20260928-120000-333"
J444, J555 = "devir-20260928-130000-444", "20260928-140000-555"
PLUGIN = {"runtime": "hermes-agent", "producer": "hermes-plugin"}
CC = {"runtime": "claude-code@hermes", "producer": "claude-code-hook"}


def _at(minutes):
    return (datetime.now(timezone.utc) - timedelta(hours=20) + timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%S.%fZ")


def _ev(cid, session, turn, job, minute, model="priced-model", work_type=None, attribution=PLUGIN):
    tags = dict(attribution)
    if job:
        tags["job_ref"] = job
    if work_type:
        tags["work_type"] = work_type
    return {"client_event_id": cid, "model": model, "session_id": session, "turn_id": turn, "prompt_tokens": 10,
            "completion_tokens": 5, "tags": tags, "occurred_at": _at(minute)}


def _seed(url):
    with httpx.Client(base_url=url, timeout=10) as c:
        c.post("/api/settings/pricing", json={"model": "priced-model", "input_price_per_1m": 1.0,
                                              "output_price_per_1m": 2.0}).raise_for_status()
        hermes = [
            _ev("a1", "s111", "t1", J111, 1, work_type="review"), _ev("a2", "s111", "t1", J111, 2, work_type="review"),
            _ev("a3", "s111", "t2", J111, 3, work_type="review"),
            # two later calls of the first task carry other jobs' ids: conflicts, counted under J111
            _ev("a4", "s111", "t1", J222, 4), _ev("a5", "s111", "t1", J333, 5),
            _ev("b1", "s222", "t1", J222, 10, work_type="code"),
            _ev("b2", "s222", "t1", J222, 11, model="unpriced-model", work_type="code"),
            _ev("c1", "s333", "t1", J333, 20, model="unpriced-model", work_type="brainstorm"),
            _ev("c2", "s333", "t1", J333, 21, model="unpriced-model", work_type="brainstorm"),
            _ev("e1", "s555", "t1", J555, 40, work_type="review"), _ev("e2", "s555", "t2", J555, 41, work_type="code"),
            _ev("n1", "s000", "t1", None, 50), _ev("n2", None, None, None, 51),
        ]
        ack = c.post("/api/events/batch", json={"events": hermes}, headers={"X-Project-Name": "hermes"}).json()
        assert (ack["inserted"], ack["rejected"]) == (len(hermes), 0), ack
        devir = [_ev("d1", "s444", "t1", J444, 30, work_type="devir", attribution=CC)]
        ack = c.post("/api/events/batch", json={"events": devir}, headers={"X-Project-Name": "pegadocrag"}).json()
        assert ack["inserted"] == 1, ack


@pytest.fixture(scope="module")
def jobs_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("jobs") / "jobs.db"
    port = _free_port()
    env = {**os.environ, "DB_PATH": str(db), "STORE_RAW_PROMPTS": "0", "TOKEN_INSPECTOR_ALLOWED_HOSTS": "127.0.0.1"}
    for name in ("INGEST_TOKEN", "STORE_TASK_PROMPTS", "JEV_ENABLED", "AI_GATEWAY_API_KEY"):
        env.pop(name, None)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(url + "/api/meta", timeout=0.5).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("server did not start")
        _seed(url)
        yield url
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture
def page(browser, jobs_server):
    context = browser.new_context()
    page = context.new_page()
    page.base = jobs_server
    yield page
    page.unroute_all(behavior="ignoreErrors")
    context.close()


ROWS = "#table-jobs tbody tr"


def _open(page):
    page.goto(page.base + "/")
    page.click('[data-view="jobs"]')
    _settled(page)


def _settled(page):
    page.wait_for_function("document.querySelectorAll('#table-jobs tbody tr').length > 0 || "
                           "getComputedStyle(document.getElementById('jobs-empty')).display !== 'none' || "
                           "getComputedStyle(document.getElementById('jobs-error')).display !== 'none'")


def _row(page, job):
    return page.locator(ROWS, has=page.locator("td.job-ref", has_text=job))


def _cells(page, job):
    return [c.strip() for c in _row(page, job).locator("td").all_inner_texts()]


def _visible(page, selector):
    return page.eval_on_selector(selector, "e => getComputedStyle(e).display !== 'none'")


def _job(ref, **over):
    item = {"job_ref": ref, "runtimes": ["hermes-agent"], "work_type": "review", "work_types": ["review"],
            "attempts": [], "projects": ["stub"], "task_count": 1, "llm_request_count": 1, "prompt_tokens": 1,
            "completion_tokens": 1, "cache_read_tokens": 0, "cache_creation_tokens": 0, "priced_count": 1,
            "unpriced_count": 0, "cost_usd": 0.5, "estimated_cost_usd": 0.0, "cost_complete": True,
            "first_event_at": "2026-09-28T10:00:00.000000Z", "last_event_at": "2026-09-28T10:00:00.000000Z",
            "conflict_count": 0, "conflict_task_count": 0}
    item.update(over)
    return item


def _body(items, total=None, page=1):
    return {"items": items, "total": len(items) if total is None else total, "page": page, "page_size": 50,
            "filters": {"runtimes": ["hermes-agent"], "work_types": ["review"]},
            "anomalies": {"job_ref_conflicts": 0, "job_ref_conflict_tasks": 0, "invalid_attribution_events": 0}}


# Header order: Job, Work type, Runtime, Projects, Tasks, Calls, Input, Cache read, Cache write, Output,
# Total (sum), Cost, Est. cost, First, Last, Attempts, Conflicts
COST, CONFLICTS = 11, 16


def test_jobs_view_lists_one_row_per_job(page):
    _open(page)
    assert page.locator(ROWS).count() == 5  # events without a job_ref never make a row
    cells = _cells(page, J111)
    assert cells[1:6] == ["review", "hermes-agent", "hermes", "2", "5"]
    assert cells[6:11] == ["50", "0", "0", "25", "75"]
    assert cells[COST].startswith("$") and not _row(page, J111).locator("td.cost-cell .badge").count()
    mixed = _row(page, J555).locator("td").nth(1)
    assert mixed.inner_text().strip() == "mixed"
    assert mixed.locator("span").get_attribute("title") == "code, review"


def test_jobs_unpriced_never_zero(page):
    _open(page)
    none = _row(page, J333).locator("td.cost-cell")
    assert none.inner_text().startswith("—") and "$0.0000" not in none.inner_text()
    assert none.locator(".badge").inner_text() == "unpriced"
    some = _row(page, J222).locator("td.cost-cell")
    assert some.inner_text().startswith("≥ $") and some.locator(".badge").inner_text() == "1 unpriced"


def test_jobs_filters_reload_whole_jobs(page):
    _open(page)
    page.select_option("#jobs-runtime", "claude-code@hermes")
    page.wait_for_function(f"document.querySelectorAll('{ROWS}').length === 1")
    assert _cells(page, J444)[3] == "pegadocrag"
    page.select_option("#jobs-runtime", "")
    page.wait_for_function(f"document.querySelectorAll('{ROWS}').length === 5")
    page.select_option("#jobs-work-type", "code")
    page.wait_for_function(f"document.querySelectorAll('{ROWS}').length === 2")
    assert _row(page, J222).count() == 1 and _row(page, J555).count() == 1
    assert _cells(page, J555)[4:6] == ["2", "2"]  # both tasks of the mixed job, not only the code task


def test_jobs_days_picker_reloads(page):
    _open(page)
    with page.expect_request(lambda r: "/api/jobs?" in r.url and "days=7" in r.url):
        page.click("#jobs-days-7")
    _settled(page)
    assert page.locator(ROWS).count() == 5


def test_jobs_error_and_empty_states(page):
    page.route("**/api/jobs?*", lambda route: route.fulfill(status=500, body="zz-server-detail-canary"))
    _open(page)
    assert _visible(page, "#jobs-error") and page.inner_text("#jobs-error") == "Could not load jobs."
    assert "zz-server-detail-canary" not in page.content()
    page.unroute("**/api/jobs?*")
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=_body([])))
    page.click("#jobs-days-90")
    page.wait_for_function("getComputedStyle(document.getElementById('jobs-empty')).display !== 'none'")
    assert not _visible(page, "#jobs-error")


def test_jobs_pagination(page):
    requests = []

    def fulfill(route):
        n = int(dict(p.split("=") for p in route.request.url.split("?")[1].split("&"))["page"])
        requests.append(n)
        route.fulfill(json=_body([_job(f"20260928-000000-{i}") for i in range(50)], total=120, page=n))

    page.route("**/api/jobs?*", fulfill)
    _open(page)
    assert page.inner_text("#jobs-page-info") == "Page 1 of 3 (120 jobs)"
    page.click("#jobs-next")
    page.wait_for_function("document.getElementById('jobs-page-info').textContent.startsWith('Page 2')")
    assert requests[-1] == 2
    page.click("#jobs-prev")
    page.wait_for_function("document.getElementById('jobs-page-info').textContent.startsWith('Page 1')")
    page.click("#jobs-next")
    page.click("#jobs-next")
    page.wait_for_function("document.getElementById('jobs-page-info').textContent.startsWith('Page 3')")
    assert page.is_disabled("#jobs-next")


def test_jobs_partial_cost_badge(page):
    page.route("**/api/jobs?*", lambda route: route.fulfill(json=_body([_job(J111, cost_complete=False,
                                                                            estimated_cost_usd=0.25)])))
    _open(page)
    cost = _row(page, J111).locator("td.cost-cell")
    assert cost.inner_text().startswith("$0.5000") and cost.locator(".badge").inner_text() == "partial"


def test_jobs_conflict_counts(page):
    _open(page)
    assert _visible(page, "#jobs-anomalies")
    assert page.inner_text("#jobs-anomalies") == (
        "1 task(s) received a different job id on 2 call(s); the first job was kept.")
    conflicts = _row(page, J111).locator("td").nth(CONFLICTS)
    assert conflicts.inner_text().startswith("2")
    assert conflicts.locator(".badge").get_attribute("title") == "2 conflicting call(s) in 1 task(s)"


# The late response ignores the abort signal (a server that answered before the abort landed), so only
# the generation check can keep it off the table.
DELAY_FILTERED = """(ms) => {
  const real = window.fetch.bind(window);
  window.fetch = (url, opts) => /runtime=hermes-agent/.test(String(url))
    ? new Promise(r => setTimeout(r, ms)).then(() => real(url))
    : real(url, opts);
}"""


def test_jobs_latest_request_wins(page):
    _open(page)
    page.evaluate(DELAY_FILTERED, 1500)
    page.select_option("#jobs-runtime", "hermes-agent")
    page.select_option("#jobs-runtime", "")
    page.wait_for_timeout(2500)
    assert page.locator(ROWS).count() == 5  # the unfiltered result, not the late filtered one (4 rows)
    assert not _visible(page, "#jobs-error")


def test_jobs_view_no_console_errors_across_views(page):
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    _open(page)
    for view in ("overview", "projects", "models", "complexity", "tasks", "settings", "jobs"):
        page.click(f'[data-view="{view}"]')
        page.wait_for_timeout(300)
    _settled(page)
    assert errors == []
