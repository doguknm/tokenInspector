"""Export (v1) panel in a real browser (O9 AC3.1, AC3.3, AC3.4, AC3.6, AC3.9): CSV built from the JSON export.

Own module-scoped server with its own DB, seeded through the ingest API. Fabricated export responses
(page.route) force the states seeding cannot produce: formula-shaped cells, server errors, broken cursors.
"""

import csv
import io
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

PLUGIN = {"runtime": "hermes-agent", "producer": "hermes-plugin"}
CC = {"runtime": "claude-code@hermes", "producer": "claude-code-hook"}
JOB = "20260928-100000-111"
EXPORT = "**/api/export/v1/**"
CANARY = "zz-canary-host.internal C:\\Users\\ZZ-CANARY-USER"

# The panel's default range: 30 UTC days ending today+1 (exclusive).
TO_DATE = (datetime.now(timezone.utc) + timedelta(days=1)).date()
FROM_DATE = TO_DATE - timedelta(days=30)
FROM, TO = f"{FROM_DATE}T00:00:00Z", f"{TO_DATE}T00:00:00Z"


def _at(hours):
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ev(cid, at, session="s", turn="t", model="priced-model", tags=None, **extra):
    body = {"client_event_id": cid, "model": model, "session_id": session, "turn_id": turn, "prompt_tokens": 10,
            "completion_tokens": 5, "tags": dict(PLUGIN) if tags is None else tags, "occurred_at": at}
    body.update(extra)
    return body


def _seed(url):
    with httpx.Client(base_url=url, timeout=30) as c:
        c.post("/api/settings/pricing", json={"model": "priced-model", "input_price_per_1m": 1.0,
                                              "output_price_per_1m": 2.0}).raise_for_status()

        def batch(events, project="hermes"):
            ack = c.post("/api/events/batch", json={"events": events}, headers={"X-Project-Name": project}).json()
            assert (ack["inserted"], ack["rejected"]) == (len(events), 0), ack

        batch([
            _ev("zero", _at(30), session="s0", prompt_tokens=0, completion_tokens=0),  # first: no job, zero tokens
            _ev("j1", _at(29), session="s1", tags={**PLUGIN, "job_ref": JOB, "work_type": "review"}),
            _ev("j2", _at(28), session="s2", tags={**PLUGIN, "job_ref": JOB, "work_type": "review"}),
            _ev("unpriced", _at(27), session="s3", model="unpriced-model"),
            _ev("formula", _at(26), session="s4", model='=HYPERLINK("x")'),
            _ev("at-from", FROM, session="s5"),  # exactly 00:00:00Z of the from date: included
            _ev("at-to", TO, session="s6"),  # exactly 00:00:00Z of the to date: excluded
        ])
        batch([_ev("cc-" + "a" * 32, _at(25), session="s7", tags=CC, ttft_ms=3)], project="pegadocrag")
        for start in range(0, 1050, 350):  # one extra project: the LLM-calls export needs two pages at 1000
            batch([_ev(f"bulk-{i}", _at(24), session=f"b{i}") for i in range(start, start + 350)], project="bulk")


@pytest.fixture(scope="module")
def export_server(tmp_path_factory):
    db = tmp_path_factory.mktemp("export") / "export.db"
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
def page(browser, export_server):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.base = export_server
    page.export_requests = []
    page.on("request", lambda r: page.export_requests.append(r.url) if "/api/export/v1/" in r.url else None)
    yield page
    page.unroute_all(behavior="ignoreErrors")
    context.close()


def _open(page, dataset="events"):
    page.goto(page.base + "/")
    page.click('[data-view="jobs"]')
    page.select_option("#export-dataset", dataset)


def _download(page):
    with page.expect_download() as info:
        page.click("#export-csv")
    with open(info.value.path(), encoding="utf-8", newline="") as f:
        text = f.read()
    assert text.startswith("\ufeff")
    return list(csv.reader(io.StringIO(text[1:], newline="")))


def _api_walk(base, dataset, start=FROM, end=TO):
    items, cursor, pages = [], None, []
    while True:
        params = {"from": start, "to": end, "limit": 1000, **({"cursor": cursor} if cursor else {})}
        page = httpx.get(f"{base}/api/export/v1/{dataset}", params=params).json()
        pages.append(page)
        items += page["items"]
        if page["complete"]:
            return pages, items
        cursor = page["next_cursor"]


def _status(page, text):
    page.wait_for_function("t => document.getElementById('export-status').textContent === t", arg=text)


def _envelope(items, fields, **over):
    body = {"schema_version": 1, "dataset": "events", "generated_at": "2026-09-28T00:00:00.000000Z",
            "period": {"from": FROM, "to": TO}, "fields": fields, "staleness": {}, "coverage": {},
            "items": items, "next_cursor": None, "complete": True}
    body.update(over)
    return body


# --- AC3.1 / AC3.3 / AC3.6: real data -------------------------------------------------------------------------


def test_csv_header_equals_fields(page):
    pages, items = _api_walk(page.base, "events")
    assert len(pages) == 2
    _open(page)
    rows = _download(page)
    header, data = rows[0], rows[1:]
    assert header == pages[0]["fields"]
    assert len(data) == len(items) == 1057
    events = [u for u in page.export_requests if "/api/export/v1/events" in u]
    assert len(events) == 2 and "cursor=" not in events[0]
    assert httpx.QueryParams(events[1].split("?", 1)[1])["cursor"] == pages[0]["next_cursor"]
    assert page.inner_text("#export-status") == "Fetched 1057 rows"
    col = {name: header.index(name) for name in header}
    by_cid = {row[col["client_event_id"]]: row for row in data}
    assert "at-from" in by_cid and "at-to" not in by_cid  # from inclusive, to exclusive (AC3.3)
    assert by_cid["cc-" + "a" * 32][col["reasoning_tokens"]] == ""  # absent: not measured
    assert by_cid["cc-" + "a" * 32][col["ttft_ms"]] == ""
    assert by_cid["unpriced"][col["cost_usd"]] == "null"  # measured, unknown
    assert by_cid["zero"][col["prompt_tokens"]] == "0" and by_cid["zero"][col["job_ref"]] == ""
    assert by_cid["formula"][col["model"]] == "null"  # the backend value rule never exports a formula


def test_csv_jobs_and_tasks_headers(page):
    for dataset in ("jobs", "tasks"):
        pages, items = _api_walk(page.base, dataset)
        _open(page, dataset)
        rows = _download(page)
        assert rows[0] == pages[0]["fields"] and len(rows) - 1 == len(items) > 0


def test_csv_empty_range_header_only(page):
    _open(page)
    page.fill("#export-from", "2020-01-01")
    page.fill("#export-to", "2020-01-31")
    rows = _download(page)
    assert len(rows) == 1 and rows[0][0] == "event_id"
    assert page.inner_text("#export-status") == "Fetched 0 rows"


def test_json_link_follows_inputs(page):
    _open(page, "tasks")
    page.fill("#export-from", "2026-09-01")
    page.fill("#export-to", "2026-09-29")
    href = page.get_attribute("#export-json", "href")
    assert href == "/api/export/v1/tasks?from=2026-09-01T00%3A00%3A00Z&to=2026-09-29T00%3A00%3A00Z"
    body = httpx.get(page.base + href).json()
    assert body["schema_version"] == 1 and body["dataset"] == "tasks"
    assert body["period"] == {"from": "2026-09-01T00:00:00.000000Z", "to": "2026-09-29T00:00:00.000000Z"}


# --- AC3.9: formula guard and RFC 4180 (intercepted, the backend never sends these) ---------------------------

FORMULAS = ['=HYPERLINK("x")', "+1+2", "-3", "@SUM(A1)", "\tx", "\rx"]


def test_csv_formula_injection_guard(page):
    fields = ["model", "prompt_tokens", "cost_usd", "error_type"]
    items = [{"model": m, "prompt_tokens": 7, "cost_usd": 0.25, "error_type": "plain"} for m in FORMULAS]
    items += [{"model": 'a,"b"', "prompt_tokens": 0, "cost_usd": None, "error_type": "line1\nline2"}]
    page.route(EXPORT, lambda route: route.fulfill(json=_envelope(items, fields)))
    _open(page)
    rows = _download(page)
    assert rows[0] == fields
    for row, formula in zip(rows[1:], FORMULAS):
        assert row[0] == "'" + formula
        assert row[1] == "7" and row[2] == "0.25" and row[3] == "plain"  # numbers and plain text unchanged
    assert rows[-1] == ['a,"b"', "0", "null", "line1\nline2"]  # quoting round-trips


# --- AC3.3: range checks and the closed error policy ---------------------------------------------------------


def test_export_range_refused_without_request(page):
    _open(page)
    page.fill("#export-from", "2026-01-01")
    page.fill("#export-to", "2026-06-01")
    page.click("#export-csv")
    _status(page, "Range must be at most 92 days")
    page.fill("#export-from", "2026-06-02")
    page.click("#export-csv")
    _status(page, "Export failed: invalid range")
    page.wait_for_timeout(300)
    assert page.export_requests == []


def test_export_error_mapped(page):
    page.route(EXPORT, lambda route: route.fulfill(status=400, json={"error": "invalid_range", "schema_version": 1}))
    _open(page)
    page.click("#export-csv")
    _status(page, "Export failed: invalid range")


ERROR_CASES = [
    ("unknown code", lambda r: r.fulfill(status=400, json={"error": CANARY}), "Export failed."),
    ("html 500", lambda r: r.fulfill(status=500, body=f"<html>{CANARY}</html>", content_type="text/html"),
     "Export failed."),
    ("not json", lambda r: r.fulfill(status=200, body=f"not json {CANARY}"), "Export failed."),
    ("network", lambda r: r.abort(), "Export failed."),
    ("expired", lambda r: r.fulfill(status=409, json={"error": "snapshot_expired", "schema_version": 1}),
     "Data changed during the export; please download again"),
]


@pytest.mark.parametrize("name,handler,message", ERROR_CASES, ids=[c[0] for c in ERROR_CASES])
def test_export_error_policy_closed(page, name, handler, message):
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    page.route(EXPORT, handler)
    _open(page)
    page.click("#export-csv")
    _status(page, message)
    assert "ZZ-CANARY-USER" not in page.content() and "zz-canary-host" not in page.content()
    assert downloads == []


# --- AC3.4 (UI): loop termination and superseded runs -------------------------------------------------------


def _paged(page, pages):
    """Serve fabricated pages in order; returns the list of served request URLs."""
    served = []

    def handler(route):
        served.append(route.request.url)
        n = len(served) - 1
        status, body = pages(n)
        route.fulfill(status=status, json=body)

    page.route(EXPORT, handler)
    return served


ONE = [{"event_id": "x"}]


def test_export_loop_terminates(page):
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    cases = [
        ("busy", lambda n: (200, _envelope(ONE, ["event_id"], complete=False, next_cursor="c1")) if n == 0
         else (503, {"error": "busy", "schema_version": 1}), "Server busy; try again shortly", 2),
        ("repeated cursor", lambda n: (200, _envelope(ONE, ["event_id"], complete=False, next_cursor="same")),
         "Export failed.", 2),
        ("missing cursor", lambda n: (200, _envelope(ONE, ["event_id"], complete=False, next_cursor=None)),
         "Export failed.", 1),
        ("row cap", lambda n: (200, _envelope([{"event_id": f"{n}-{i}"} for i in range(1000)], ["event_id"],
                                              complete=False, next_cursor=f"c{n}")),
         "Too many rows; narrow the range", 100),
    ]
    for name, pages, message, expected_requests in cases:
        page.unroute_all(behavior="ignoreErrors")
        served = _paged(page, pages)
        _open(page)
        page.click("#export-csv")
        _status(page, message)
        page.wait_for_timeout(300)
        assert len(served) == expected_requests, name
    assert downloads == []


# The delayed response ignores the abort signal (a server that answered before the abort landed), so only
# the run id can stop the superseded run.
DELAY_EVENTS = """(ms) => {
  const real = window.fetch.bind(window);
  window.fetch = (url, opts) => /export\\/v1\\/events/.test(String(url))
    ? new Promise(r => setTimeout(r, ms)).then(() => real(url))
    : real(url, opts);
}"""


def test_export_superseded_run_stops(page):
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    _open(page)
    page.evaluate(DELAY_EVENTS, 1000)
    page.click("#export-csv")  # run 1: LLM calls (two pages)
    page.select_option("#export-dataset", "jobs")
    with page.expect_download() as info:
        page.click("#export-csv")  # run 2: jobs
    assert info.value.suggested_filename.startswith("ti-export-v1-jobs-")
    page.wait_for_timeout(2500)  # run 1's late first page arrives now
    events = [u for u in page.export_requests if "/api/export/v1/events" in u]
    assert len(events) == 1  # run 1 made no request after run 2 started
    assert len(downloads) == 1 and page.inner_text("#export-status").startswith("Fetched ")
    jobs_rows = len(_api_walk(page.base, "jobs")[1])
    assert page.inner_text("#export-status") == f"Fetched {jobs_rows} rows"


def test_export_run_parameters_frozen(page):
    _open(page)
    page.evaluate(DELAY_EVENTS, 800)
    with page.expect_download():
        page.click("#export-csv")
        page.fill("#export-from", "2020-01-01")  # changed while page 1 is in flight
    events = [u for u in page.export_requests if "/api/export/v1/events" in u]
    assert len(events) == 2
    assert all(httpx.QueryParams(u.split("?", 1)[1])["from"] == FROM for u in events)
