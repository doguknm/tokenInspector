"""O9 Phase 3: versioned read-only export v1 — contract, allowlists, value rules, envelope (X2-X5)."""

import ast
import base64
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

import features
from database import AsyncSessionLocal
from routes import export as export_module
from test_job_correlation import JOB_A, JOB_B, rows

START, END = "2026-09-01T00:00:00Z", "2026-09-29T00:00:00Z"
START_N, END_N = "2026-09-01T00:00:00.000000Z", "2026-09-29T00:00:00.000000Z"
PRICED, UNPRICED = "claude-sonnet-4-6", "zz-export-unpriced-model"
PLUGIN = {"runtime": "hermes-agent", "producer": "hermes-plugin"}
CC = {"runtime": "claude-code@windows", "producer": "claude-code-hook"}
DATASETS = ("jobs", "tasks", "events")

# Pinned literal field lists (the CSV header): a change here is a contract change.
FIELDS = {
    "events": [
        "event_id", "client_event_id", "project_name", "session_id", "task_ref", "runtime", "runtime_inferred",
        "producer", "job_ref", "job_ref_conflict", "work_type", "job_attempt", "occurred_at", "recorded_at",
        "provider", "model", "pricing_model", "status", "error_type", "http_status", "prompt_tokens",
        "completion_tokens", "cache_read_tokens", "cache_creation_tokens", "reasoning_tokens", "cost_status",
        "cost_usd", "estimated_cost_usd", "process_time_ms", "ttft_ms", "attempt", "retry_count", "tool_call_count",
        "role", "complexity", "complexity_method"],
    "tasks": [
        "task_ref", "project_name", "session_id", "runtimes", "runtime_inferred", "job_ref", "work_type",
        "work_types", "hierarchy_status", "parent_task_ref", "root_task_ref", "first_event_at", "last_event_at",
        "wall_time_ms", "completion", "completed_at", "llm_request_count", "prompt_tokens", "completion_tokens",
        "cache_read_tokens", "cache_creation_tokens", "priced_count", "unpriced_count", "cost_usd",
        "estimated_cost_usd", "cost_complete", "conflict_count", "updated_at"],
    "jobs": [
        "job_ref", "runtimes", "runtime_inferred", "work_type", "work_types", "attempts", "projects", "task_count",
        "llm_request_count", "prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_creation_tokens",
        "priced_count", "unpriced_count", "cost_usd", "estimated_cost_usd", "cost_complete", "first_event_at",
        "last_event_at", "conflict_count", "conflict_task_count", "updated_at"],
}


def ev(cid, *, at="2026-09-10T10:00:00Z", session="s1", turn="t1", tags=None, model=PRICED, **extra):
    """An ingest body; `tags=None` means a tagged hermes-agent event, `{}` a legacy (untagged) one."""
    body = {"client_event_id": cid, "model": model, "session_id": session, "turn_id": turn, "prompt_tokens": 10,
            "completion_tokens": 5, "occurred_at": at, "tags": dict(PLUGIN) if tags is None else tags}
    body.update(extra)
    return body


async def post(client, *bodies, project="hermes"):
    response = await client.post("/api/events/batch", json={"events": list(bodies)}, headers={"X-Project-Name": project})
    assert response.status_code == 200, response.text
    assert response.json()["rejected"] == 0, response.text
    return response.json()


async def export(client, dataset, start=START, end=END, **params):
    response = await client.get(f"/api/export/v1/{dataset}", params={"from": start, "to": end, **params})
    assert response.status_code == 200, response.text
    return response.json()


async def walk(client, dataset, start=START, end=END, limit=1000):
    """Follow next_cursor to the end; returns (items, pages)."""
    items, pages, cursor = [], 0, None
    while True:
        params = {"limit": limit} if cursor is None else {"limit": limit, "cursor": cursor}
        page = await export(client, dataset, start, end, **params)
        items += page["items"]
        pages += 1
        if page["complete"]:
            assert page["next_cursor"] is None
            return items, pages
        assert page["next_cursor"]
        cursor = page["next_cursor"]


def by(items, key):
    return {item[key]: item for item in items}


# --- AC3.1 ----------------------------------------------------------------------------------------------------


async def test_export_endpoints_read_only_no_auth(client, monkeypatch):
    await post(client, ev("a", tags={**PLUGIN, "job_ref": JOB_A}))
    counts = await rows("SELECT (SELECT COUNT(*) FROM token_events), (SELECT COUNT(*) FROM tasks)")
    for token in (None, "x" * 32):
        if token:
            monkeypatch.setenv("INGEST_TOKEN", token)
        features.refresh()
        for dataset in DATASETS:
            page = await export(client, dataset)  # no token header sent
            assert page["schema_version"] == 1 and len(page["items"]) == 1
            for method in ("POST", "PUT", "DELETE", "PATCH"):
                response = await client.request(method, f"/api/export/v1/{dataset}", params={"from": START, "to": END})
                assert response.status_code == 405
    monkeypatch.delenv("INGEST_TOKEN")
    features.refresh()
    assert await rows("SELECT (SELECT COUNT(*) FROM token_events), (SELECT COUNT(*) FROM tasks)") == counts
    assert (await client.get("/api/export/v1/nope", params={"from": START, "to": END})).status_code == 404


# --- AC3.2 ----------------------------------------------------------------------------------------------------

CANARIES = ("ZZ-ERROR-MESSAGE", "ZZ-RAW-PROMPT", "ZZ-TASK-PROMPT", "ZZ-LABEL-NOTE", "ZZ-TOOL-NAME", "zz-extra-tag",
            "zz-trace", "zz-span", "ZZ-FINISH", "zz-user-hash", "zz-env", "zz-platform", "zz-tool-call",
            "zz-api-request", "zz-task-id")


async def test_export_field_allowlist_contract(client):
    await post(client, ev("full", tags={**PLUGIN, "job_ref": JOB_A, "work_type": "review", "job_attempt": 2,
                                        "zz_extra": "zz-extra-tag"},
                          error_message="ZZ-ERROR-MESSAGE", trace_id="zz-trace", span_id="zz-span",
                          parent_span_id="zz-span", finish_reason="ZZ-FINISH", user_id_hash="zz-user-hash",
                          environment="zz-env", platform="zz-platform", tool_call_id="zz-tool-call",
                          api_request_id="zz-api-request", task_id="zz-task-id", ttft_ms=5, process_time_ms=9,
                          http_status=200, tool_call_count=1, complexity=3, complexity_method="request-shape-v1",
                          role="primary", provider="anthropic", request_tool_names=["ZZ-TOOL-NAME"],
                          prompt_hash="a" * 64, prompt_length=12, reasoning_tokens=1, cache_read_tokens=2,
                          cache_creation_tokens=3))
    async with AsyncSessionLocal() as session:  # columns ingest never fills with text while capture is off
        await session.execute(text("UPDATE token_events SET prompt_text = 'ZZ-RAW-PROMPT'"))
        await session.execute(text("UPDATE tasks SET prompt_text = 'ZZ-TASK-PROMPT'"))
        await session.execute(text(
            "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, labeler, note, "
            "evaluated_at, http_attempts) SELECT 'lab', id, 'human', 'r', 'ok', 'me', 'ZZ-LABEL-NOTE', 'x', 0 "
            "FROM tasks"))
        await session.commit()
    for dataset in DATASETS:
        response = await client.get(f"/api/export/v1/{dataset}", params={"from": START, "to": END})
        body = response.json()
        assert body["fields"] == FIELDS[dataset] == list(export_module.EXPORT_FIELDS[dataset])
        assert len(body["items"]) == 1
        for item in body["items"]:
            assert set(item) <= set(FIELDS[dataset])
        for canary in CANARIES:
            assert canary not in response.text, (dataset, canary)
    (item,) = (await export(client, "events"))["items"]
    assert set(item) == set(FIELDS["events"])  # every field present for a fully measured plugin event


async def test_export_tag_allowlist(client):
    tags = {**PLUGIN, "job_ref": JOB_A, "work_type": "code", "job_attempt": 1,
            **{f"zz_k{i}": f"zz-tag-value-{i}" for i in range(7)}}
    assert len(tags) == 12
    await post(client, ev("t", tags=tags))
    response = await client.get("/api/export/v1/events", params={"from": START, "to": END})
    (item,) = response.json()["items"]
    assert "tags" not in item and "zz-tag-value" not in response.text and "zz_k" not in response.text
    assert {k: item[k] for k in export_module.EXPORT_TAG_KEYS} == {
        "runtime": "hermes-agent", "producer": "hermes-plugin", "job_ref": JOB_A, "work_type": "code",
        "job_attempt": 1}


def test_export_not_reusing_events_listing():
    tree = ast.parse(Path(export_module.__file__).read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "list_events" not in names | imported


async def test_export_value_canaries(client):
    path_model, host_model = "C:\\Users\\ZZ-CANARY-USER\\m", "zz-canary-host.internal"
    path_session, at_cid = "C:\\Users\\ZZ-CANARY-USER\\s", "zz@canary-cid"
    await post(client,
               ev("c1", session=path_session, model=path_model, status="error",
                  error_type="ZZ canary free text /home/zz-canary"),
               ev(at_cid, session="ok_session-1", turn="t2", model=host_model, status="error",
                  error_type="openai.RateLimitError"),
               ev("C:\\Users\\ZZ-CANARY-USER\\cid", session="s3", turn="t3"))
    first = await client.get("/api/export/v1/events", params={"from": START, "to": END})
    second = await client.get("/api/export/v1/events", params={"from": START, "to": END})
    items = first.json()["items"]
    for canary in ("ZZ-CANARY-USER", "/home/zz-canary", "free text", "zz@canary"):
        assert canary not in first.text, canary
    pseudo = [i for i in items if i["session_id"].startswith("p-")]
    assert len(pseudo) == 1 and len(pseudo[0]["session_id"]) == 34
    assert pseudo[0]["model"] is None and pseudo[0]["pricing_model"] is None  # path-shaped model -> unknown
    assert pseudo[0]["error_type"] == "other"
    host = next(i for i in items if i["session_id"] == "ok_session-1")
    assert host["model"] == host_model  # the documented single-label/FQDN residual: shape cannot tell it apart
    assert host["error_type"] == "RateLimitError" and host["client_event_id"].startswith("p-")
    assert sum(i["client_event_id"].startswith("p-") for i in items) == 2
    assert first.json()["items"] == second.json()["items"]  # deterministic pseudonyms
    tasks = await client.get("/api/export/v1/tasks", params={"from": START, "to": END})
    assert "ZZ-CANARY-USER" not in tasks.text


# --- AC3.3 ----------------------------------------------------------------------------------------------------


async def test_range_bounds_inclusive_exclusive(client):
    await post(client, ev("at-from", at=START, turn="a"), ev("at-to", at=END, turn="b"),
               ev("before", at="2026-08-31T23:59:59.999999Z", turn="c"))
    page = await export(client, "events", start="2026-09-01T03:00:00+03:00", end="2026-09-29T03:00:00+03:00")
    assert page["period"] == {"from": START_N, "to": END_N}
    assert [i["client_event_id"] for i in page["items"]] == ["at-from"]
    assert page["generated_at"].endswith("Z") and page["items"][0]["occurred_at"] == START_N


async def test_cohort_period_semantics(client):
    await post(client,
               # job A starts before the period; its in-period call is still an event of the period
               ev("x0", at="2026-08-30T10:00:00Z", session="sx", turn="tx", tags={**PLUGIN, "job_ref": JOB_A}),
               ev("x1", at="2026-09-05T10:00:00Z", session="sx", turn="tx", tags={**PLUGIN, "job_ref": JOB_A}),
               # job B starts inside the period and has a call after `to`
               ev("y1", at="2026-09-20T10:00:00Z", session="sy", turn="ty", tags={**PLUGIN, "job_ref": JOB_B}),
               ev("y2", at="2026-10-02T10:00:00Z", session="sy", turn="ty", tags={**PLUGIN, "job_ref": JOB_B},
                  prompt_tokens=100),
               ev("from", at=START, session="sf", turn="tf"), ev("to", at=END, session="st", turn="tt"))
    events = by((await export(client, "events"))["items"], "client_event_id")
    assert set(events) == {"x1", "y1", "from"}
    jobs = by((await export(client, "jobs"))["items"], "job_ref")
    assert set(jobs) == {JOB_B}
    assert jobs[JOB_B]["llm_request_count"] == 2 and jobs[JOB_B]["prompt_tokens"] == 110  # includes after `to`
    assert jobs[JOB_B]["last_event_at"] == "2026-10-02T10:00:00.000000Z"
    tasks = (await export(client, "tasks"))["items"]
    assert sorted(t["first_event_at"] for t in tasks) == [START_N, "2026-09-20T10:00:00.000000Z"]
    (y,) = [t for t in tasks if t["first_event_at"].startswith("2026-09-20")]
    assert y["llm_request_count"] == 2 and y["prompt_tokens"] == 110


ERROR_CASES = [
    ({"to": END}, 400, "invalid_range"),
    ({"from": "zz-canary-host.internal C:\\Users\\ZZ-CANARY-USER", "to": END}, 400, "invalid_range"),
    ({"from": "2026-09-01T00:00:00", "to": END}, 400, "invalid_range"),  # no timezone
    ({"from": END, "to": START}, 400, "invalid_range"),
    ({"from": START, "to": START}, 400, "invalid_range"),
    ({"from": START, "to": "2026-12-03T00:00:00Z"}, 400, "invalid_range"),  # 93 days
    ({"from": START, "to": END, "limit": "0"}, 400, "invalid_limit"),
    ({"from": START, "to": END, "limit": "1001"}, 400, "invalid_limit"),
    ({"from": START, "to": END, "limit": "abc"}, 400, "invalid_limit"),
    ({"from": START, "to": END, "cursor": "zz-canary-garbage!!"}, 400, "invalid_cursor"),
]


async def test_export_errors_static_no_echo(client, caplog):
    for params, status, code in ERROR_CASES:
        response = await client.get("/api/export/v1/events", params=params)
        assert response.status_code == status and response.json() == {"error": code, "schema_version": 1}, params
        assert "canary" not in response.text
    assert (await client.get("/api/export/v1/events",
                             params={"from": START, "to": "2026-12-02T00:00:00Z"})).status_code == 200  # 92 days
    await post(client, *(ev(f"e{i}", turn=f"t{i}", tags={**PLUGIN, "job_ref": JOB_A}) for i in range(3)))
    for dataset in DATASETS:  # cursors are bound to their dataset and range
        cursor = (await export(client, dataset, limit=1)).get("next_cursor")
        if cursor is None:
            continue
        for other in DATASETS:
            if other != dataset:
                response = await client.get(f"/api/export/v1/{other}", params={"from": START, "to": END,
                                                                               "cursor": cursor})
                assert response.json() == {"error": "invalid_cursor", "schema_version": 1}
        response = await client.get(f"/api/export/v1/{dataset}", params={"from": START, "to": "2026-09-28T00:00:00Z",
                                                                         "cursor": cursor})
        assert response.status_code == 400 and response.json()["error"] == "invalid_cursor"
    assert "canary" not in caplog.text


async def test_export_oversized_numbers_rejected(client):
    """code-r1 p1-F3: a huge limit or an out-of-int64 cursor value is a static 400, never a 500."""
    response = await client.get("/api/export/v1/events", params={"from": START, "to": END, "limit": "9" * 5000})
    assert response.status_code == 400 and response.json() == {"error": "invalid_limit", "schema_version": 1}
    await post(client, *(ev(f"o{i}", turn=f"o{i}", tags=PLUGIN) for i in range(2)))
    cursor = (await export(client, "events", limit=1))["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    for key in ("s", "r", "k"):
        for bad in (2**63, -1):
            forged = base64.urlsafe_b64encode(json.dumps({**payload, key: bad}).encode()).decode().rstrip("=")
            response = await client.get("/api/export/v1/events", params={"from": START, "to": END, "cursor": forged})
            assert response.status_code == 400 and response.json()["error"] == "invalid_cursor", (key, bad)


# --- AC3.5 / AC3.6 --------------------------------------------------------------------------------------------


async def test_envelope_staleness_coverage(client):
    await post(client, ev("h1", turn="a"), ev("w1", turn="b", tags=CC, client_event_id="cc-" + "1" * 32))
    page = await export(client, "events")
    latest = dict(await rows(
        "SELECT json_extract(tags_json, '$.runtime'), MAX(recorded_at) FROM token_events GROUP BY 1"))
    assert page["staleness"] == {"hermes-agent": latest["hermes-agent"],
                                 "claude-code@windows": latest["claude-code@windows"],
                                 "claude-code@hermes": None, "app": None}
    assert page["coverage"] == {"measured": ["hermes-agent", "claude-code@windows"],
                                "not_measured": ["claude-code@hermes", "app"]}
    assert page["schema_version"] == 1 and page["dataset"] == "events" and page["generated_at"].endswith("Z")
    empty = await export(client, "jobs", start="2025-01-01T00:00:00Z", end="2025-02-01T00:00:00Z")
    assert empty["coverage"]["measured"] == [] and empty["items"] == []
    assert empty["staleness"]["hermes-agent"] == latest["hermes-agent"]  # staleness is all-time


async def test_legacy_events_inferred_runtime(client):
    await post(client, ev("legacy", tags={}, at="2026-09-10T10:00:00Z"),
               ev("old", tags={}, at="2026-07-01T10:00:00Z", turn="t0"))
    page = await export(client, "events")
    (item,) = page["items"]
    assert item["runtime"] == "hermes-agent" and item["runtime_inferred"] is True and "producer" not in item
    assert page["coverage"]["measured"] == ["hermes-agent"]
    july = await export(client, "events", start="2026-08-01T00:00:00Z", end="2026-08-02T00:00:00Z")
    assert july["items"] == [] and july["staleness"]["hermes-agent"] is not None  # all-time staleness
    await post(client, ev("tagged", turn="t9"))
    tagged = by((await export(client, "events"))["items"], "client_event_id")["tagged"]
    assert tagged["runtime_inferred"] is False and tagged["producer"] == "hermes-plugin"


async def test_zero_unknown_absent_distinct(client):
    await post(client,
               ev("zero", turn="a", prompt_tokens=0, completion_tokens=0),
               ev("unpriced", turn="b", model=UNPRICED),
               ev("cc-" + "2" * 32, turn="c", tags=CC, ttft_ms=7, reasoning_tokens=0),
               ev("no-task", session=None, turn=None))
    items = by((await export(client, "events"))["items"], "client_event_id")
    zero = items["zero"]
    assert zero["prompt_tokens"] == 0 and zero["completion_tokens"] == 0 and type(zero["prompt_tokens"]) is int
    assert zero["cost_status"] == "priced" and zero["cost_usd"] == 0.0
    assert zero["reasoning_tokens"] == 0 and zero["ttft_ms"] is None  # measured zero vs. not reported (null)
    assert items["unpriced"]["cost_usd"] is None and items["unpriced"]["cost_status"] == "unpriced"
    assert items["unpriced"]["estimated_cost_usd"] is None
    cc = items["cc-" + "2" * 32]
    assert "reasoning_tokens" not in cc and "ttft_ms" not in cc and cc["producer"] == "claude-code-hook"
    assert "job_ref" not in zero and "work_type" not in zero and "job_attempt" not in zero
    assert "task_ref" not in items["no-task"] and len(zero["task_ref"]) == 32
    assert all(type(i[k]) is int for i in items.values() for k in ("prompt_tokens", "completion_tokens",
                                                                    "cache_read_tokens", "cache_creation_tokens"))


async def test_event_job_ref_is_canonical(client):
    await post(client, ev("first", tags={**PLUGIN, "job_ref": JOB_A}),
               ev("later", tags={**PLUGIN, "job_ref": JOB_B}, at="2026-09-10T11:00:00Z"))
    response = await client.get("/api/export/v1/events", params={"from": START, "to": END})
    items = by(response.json()["items"], "client_event_id")
    assert items["later"]["job_ref"] == JOB_A and items["later"]["job_ref_conflict"] is True
    assert items["first"]["job_ref"] == JOB_A and items["first"]["job_ref_conflict"] is False
    assert JOB_B not in response.text
    jobs_response = await client.get("/api/export/v1/jobs", params={"from": START, "to": END})
    (job,) = jobs_response.json()["items"]
    assert job["job_ref"] == JOB_A and job["llm_request_count"] == 2 and JOB_B not in jobs_response.text
    assert job["conflict_count"] == 1 and job["conflict_task_count"] == 1
    (task,) = (await export(client, "tasks"))["items"]
    assert task["job_ref"] == JOB_A and task["conflict_count"] == 1


async def test_task_multi_value_fields(client):
    await post(client,
               ev("l1", session="s1", turn="t1", tags={}),
               ev("l2", session="s1", turn="t1"),
               ev("w1", session="s2", turn="t2", tags={**PLUGIN, "work_type": "review"}),
               ev("w2", session="s2", turn="t2", tags={**PLUGIN, "work_type": "code"}),
               ev("n1", session="s3", turn="t3"))
    tasks = {t["session_id"]: t for t in (await export(client, "tasks"))["items"]}
    assert tasks["s1"]["runtimes"] == ["hermes-agent"] and tasks["s1"]["runtime_inferred"] is True
    assert tasks["s2"]["work_type"] is None and tasks["s2"]["work_types"] == ["code", "review"]
    assert tasks["s2"]["runtime_inferred"] is False
    assert "work_type" not in tasks["s3"] and tasks["s3"]["work_types"] == [] and "job_ref" not in tasks["s3"]


async def test_export_excludes_evaluator(client):
    await post(client, ev("jev-1", role="evaluator", tags={"purpose": "evaluator", "job_ref": JOB_A}),
               project="token-inspector")
    assert await rows("SELECT COUNT(*) FROM token_events") == [(1,)]
    for dataset in DATASETS:
        page = await export(client, dataset)
        assert page["items"] == [] and page["coverage"]["measured"] == []
        assert set(page["staleness"].values()) == {None}


async def test_stable_ids_and_updated_at(client):
    await post(client, ev("a", tags={**PLUGIN, "job_ref": JOB_A}, model="zz-export-recost-model"),
               ev("cc-" + "3" * 32, turn="t2", tags={**CC, "job_ref": JOB_A}))
    runs = [{d: await export(client, d) for d in DATASETS} for _ in range(2)]
    for d, key in (("events", "event_id"), ("tasks", "task_ref"), ("jobs", "job_ref")):
        assert [i[key] for i in runs[0][d]["items"]] == [i[key] for i in runs[1][d]["items"]]
    assert {i["client_event_id"] for i in runs[0]["events"]["items"]} == {"a", "cc-" + "3" * 32}
    task_before = by(runs[0]["tasks"]["items"], "session_id")["s1"]["updated_at"]
    await post(client, ev("a2", at="2026-09-10T12:00:00Z", tags={**PLUGIN, "job_ref": JOB_A}))
    tasks = by((await export(client, "tasks"))["items"], "session_id")
    assert tasks["s1"]["updated_at"] > task_before  # a later event on an open task moves it
    job_before = (await export(client, "jobs"))["items"][0]
    rule = {"model": "zz-export-recost-model", "input_price_per_1m": 1.0, "output_price_per_1m": 1.0}
    assert (await client.post("/api/settings/pricing", json=rule)).status_code == 200
    assert (await client.post("/api/settings/recost", params={"dry_run": "false"})).json()["changed"] == 1
    job_after = (await export(client, "jobs"))["items"][0]
    assert job_after["cost_usd"] != job_before["cost_usd"]
    assert job_after["updated_at"] == job_before["updated_at"]  # informational only, not a change marker


# --- AC3.8 ----------------------------------------------------------------------------------------------------


async def test_export_busy_returns_503(client, monkeypatch):
    async def locked(_session):
        raise OperationalError("SELECT", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(export_module, "_state", locked)
    response = await client.get("/api/export/v1/events", params={"from": START, "to": END})
    assert response.status_code == 503 and response.headers["retry-after"] == "5"
    assert response.json() == {"error": "busy", "schema_version": 1}
    assert json.loads(response.text) == {"error": "busy", "schema_version": 1}
