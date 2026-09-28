"""O10 backend: per-project prompt/JEV allowlist, proven-root rule, child-transition null, /api/meta state.

Plans/prompt-allowlist-safe-producer (AC6-AC9, AC14, AC15). Privacy assertions use a unique canary per test and
check the prompt field independently of the marker (F12).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import database
import features
import jev_scorer
import retention
import task_store
from database import AsyncSessionLocal
from main import app, lifespan
from test_jev_worker import Clock, Gateway, ok_from, rate_limited

TOKEN = "t" * 32
MARKER = "ZZ-ALLOWLIST-CANARY"
SECRET = "sk-ant-CANARYSECRET0123456789"
ROOT = {"task_hierarchy": "root", "prompt_eligibility": task_store.PROMPT_ELIGIBILITY}


def canary(n: int) -> str:
    return f"{MARKER}-{n} please use {SECRET} at /srv/private/x"


def _headers(project: str, token: bool = True) -> dict:
    return {"X-Project-Name": project, **({"X-Ingest-Token": TOKEN} if token else {})}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ev(cid, turn="s1:t:1", session="s1", **extra):
    body = {"client_event_id": cid, "model": "m", "session_id": session, "turn_id": turn,
            "occurred_at": "2026-09-20T10:00:00Z", "prompt_tokens": 1}
    body.update(extra)
    return body


def _prompt(n, **extra):
    return {"task_prompt_text": canary(n), "task_prompt_captured_at": _now_iso(), **extra}


def _capture(monkeypatch, allowed="x"):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    if allowed is None:
        monkeypatch.delenv("TASK_PROMPT_ALLOWED_PROJECTS", raising=False)
    else:
        monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", allowed)
    return features.refresh()


async def _rows(sql, **params):
    async with AsyncSessionLocal() as session:
        return [tuple(r) for r in (await session.execute(text(sql), params)).all()]


async def _exec(sql, **params):
    async with AsyncSessionLocal() as session:
        await session.execute(text(sql), params)
        await session.commit()


async def _run_eval(refs, run_id="run"):
    """run_evaluation needs its evaluator_runs row (task_evaluations.run_id is a foreign key)."""
    await _exec("INSERT INTO evaluator_runs (id, evaluator, status, requested_count, queued_count, queued_at) "
                "VALUES (:id, 'jev', 'queued', :n, :n, '2026-09-27T00:00:00Z')", id=run_id, n=len(refs))
    await jev_scorer.run_evaluation(run_id, refs)
    (row,) = await _rows("SELECT status, stop_reason FROM evaluator_runs WHERE id = :id", id=run_id)
    assert row != ("stopped", "error")  # the run itself never crashed


def _db_bytes() -> bytes:
    db = Path(database.DB_PATH)
    wal = db.with_name(db.name + "-wal")
    return db.read_bytes() + (wal.read_bytes() if wal.exists() else b"")


def _absent(n: int) -> None:
    raw = _db_bytes()
    assert f"{MARKER}-{n} ".encode() not in raw  # the trailing space keeps n unique (601 vs 6011)


async def _events_text() -> str:
    rows = await _rows("SELECT * FROM token_events")
    return json.dumps([list(r) for r in rows], default=str)


async def _post(client, body, project="x"):
    response = await client.post("/api/events", json=body, headers=_headers(project))
    assert response.status_code in (200, 201), response.text
    return response


async def _post_batch(client, events, project="x"):
    response = await client.post("/api/events/batch", json={"events": events}, headers=_headers(project))
    assert response.status_code == 200, response.text
    return response.json()


async def _prompt_of(project, session="s1", turn="s1:t:1"):
    rows = await _rows("SELECT prompt_text FROM tasks WHERE id = :id", id=task_store.task_ref(project, session, turn))
    return rows[0][0] if rows else "NO-ROW"


@pytest.fixture
def outcomes(monkeypatch):
    seen = []
    original = task_store.on_event_inserted

    async def spy(*args, **kwargs):
        result = await original(*args, **kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(task_store, "on_event_inserted", spy)
    return seen


@pytest.fixture(autouse=True)
def _refresh_after():
    yield
    features.refresh()


# --- AC6 ingest ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", " , "])
async def test_empty_allowlist_accepts_event_but_stores_no_prompt(client, monkeypatch, value):
    if value is None:
        state = _capture(monkeypatch, None)
    else:
        state = _capture(monkeypatch, value)
    assert (state.capture_enabled, state.capture_disabled_reason) == (False, "no_allowed_projects")
    single = await client.post("/api/events", json=_ev("a", **ROOT, **_prompt(601)), headers=_headers("x"))
    assert single.status_code == 201
    batch = await _post_batch(client, [_ev("b", turn="s1:t:2", **ROOT, **_prompt(601))])
    assert batch["inserted"] == 1
    assert await _rows("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL") == [(0,)]
    assert MARKER not in await _events_text()
    _absent(601)


async def test_not_listed_project_prompt_is_stripped(client, monkeypatch):
    _capture(monkeypatch, "x")
    await _post(client, _ev("y1", **ROOT, **_prompt(602)), project="y")
    assert await _prompt_of("y") is None
    _absent(602)
    await _post(client, _ev("x1", **ROOT, task_prompt_text=f"x prompt {SECRET}", task_prompt_captured_at=_now_iso()))
    ((stored, captured, expires),) = await _rows(
        "SELECT prompt_text, prompt_captured_at, prompt_expires_at FROM tasks WHERE project_name = 'x'")
    assert SECRET not in stored and "[REDACTED:secret]" in stored
    parse = lambda v: datetime.fromisoformat(v.replace("Z", "+00:00"))  # noqa: E731
    assert parse(expires) - parse(captured) == timedelta(days=30)


@pytest.mark.parametrize("endpoint", ["single", "batch"])
async def test_single_batch_replay_and_duplicate_behave_the_same(client, monkeypatch, endpoint):
    _capture(monkeypatch, "x")

    async def send(body, project):
        if endpoint == "single":
            return (await _post(client, body, project)).status_code
        return await _post_batch(client, [body], project)

    await send(_ev("dup", **ROOT, **_prompt(603)), "y")
    await send(_ev("ok", turn="s1:t:2", **ROOT, **_prompt(6031)), "x")
    assert await _prompt_of("y") is None
    assert (await _prompt_of("x", turn="s1:t:2")).startswith(f"{MARKER}-6031")
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "x,y")
    features.refresh()
    again = await send(_ev("dup", **ROOT, **_prompt(603)), "y")  # duplicate delivery: derivation is skipped
    assert again == 200 if endpoint == "single" else again["duplicates"] == 1
    assert await _prompt_of("y") is None
    _absent(603)


async def test_validation_errors_never_echo_prompt(client, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _capture(monkeypatch, "x")
    too_long = canary(604) + "z" * 200_001
    bad_other = _ev("b", **ROOT, **_prompt(604), prompt_tokens=-1)
    for body in (_ev("a", **ROOT, task_prompt_text=too_long), bad_other):
        single = await client.post("/api/events", json=body, headers=_headers("x"))
        assert single.status_code == 422
        assert MARKER not in single.text and "CANARYSECRET" not in single.text
        assert single.json()["detail"]  # the usual 422 shape
    batch = await _post_batch(client, [_ev("c", **ROOT, task_prompt_text=too_long), bad_other])
    assert batch["rejected"] == 2
    dumped = json.dumps(batch)
    assert MARKER not in dumped and "CANARYSECRET" not in dumped
    assert "task_prompt_text" in batch["items"][0]["error"]  # location and type are kept
    assert MARKER not in caplog.text and "CANARYSECRET" not in caplog.text
    assert await _rows("SELECT COUNT(*) FROM tasks") == [(0,)]
    _absent(604)


# --- AC14 marker / AC15 old plugin -------------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [None, "v0", "v" * 10_000])
async def test_prompt_without_marker_is_not_stored(client, monkeypatch, outcomes, marker):
    _capture(monkeypatch, "x")
    body = _ev("a", task_hierarchy="root", **_prompt(605))
    if marker is not None:
        body["prompt_eligibility"] = marker
    assert (await _post(client, body)).status_code == 201
    assert await _prompt_of("x") is None
    assert outcomes[-1]["prompt"] == "discarded_no_eligibility"
    await _post(client, _ev("b", **ROOT, **_prompt(6051)))
    assert (await _prompt_of("x")).startswith(f"{MARKER}-6051")
    for table in ("token_events", "tasks"):
        columns = [r[1] for r in await _rows(f"PRAGMA table_info({table})")]
        assert "prompt_eligibility" not in columns
    assert "v1-allowed" not in await _events_text()


async def test_old_plugin_payload_cannot_store_prompt(client, monkeypatch):
    _capture(monkeypatch, "x")
    old = _ev("a", task_hierarchy="root", **_prompt(606))  # pre-upgrade plugin: prompt, no marker
    assert (await _post(client, old)).status_code == 201
    assert await _prompt_of("x") is None
    _absent(606)


# --- AC7 proven root ---------------------------------------------------------------------------------------------


async def test_child_never_stores_prompt(client, monkeypatch, outcomes):
    _capture(monkeypatch, "x,y")
    await _post(client, _ev("p", **ROOT))  # listed parent root x/s1/s1:t:1
    parent = task_store.task_ref("x", "s1", "s1:t:1")
    cases = [
        ("a", "x", _ev("a", turn="c:t:1", session="c", parent_session_id="s1", parent_turn_id="s1:t:1",
                       prompt_eligibility="v1-allowed", **_prompt(607))),
        ("b", "x", _ev("b", turn="d:t:1", session="d", task_hierarchy="child", prompt_eligibility="v1-allowed",
                       **_prompt(607))),
        ("c", "x", _ev("c", turn="u:t:1", session="u", prompt_eligibility="v1-allowed", **_prompt(607))),
        ("d", "y", _ev("d", turn="e:t:1", session="e", parent_session_id="s1", parent_turn_id="s1:t:1",
                       prompt_eligibility="v1-allowed", **_prompt(607))),
    ]
    for label, project, body in cases:
        assert (await _post(client, body, project)).status_code == 201, label
        assert outcomes[-1]["prompt"] == "discarded_not_eligible", label
    rows = {r[0]: r[1:] for r in await _rows(
        "SELECT session_id, hierarchy_status, parent_task_ref, prompt_text FROM tasks")}
    assert rows["c"] == ("child", parent, None)
    assert rows["d"] == ("child", None, None)
    assert rows["u"] == ("unknown", None, None)
    assert rows["e"][0] == "child" and rows["e"][2] is None  # Q1: parent ref built from y, prompt never stored
    _absent(607)


async def _craft(ref, project="x", hierarchy="root", parent=None, root=None, prompt=None):
    stamp = "2026-09-20T10:00:00.000000Z"
    expires = (datetime.now(timezone.utc) + timedelta(days=20)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    await _exec(
        "INSERT INTO tasks (id, project_name, session_id, turn_id, parent_task_ref, root_task_ref, hierarchy_status, "
        "first_seen_at, last_seen_at, prompt_text, prompt_captured_at, prompt_expires_at, created_at, updated_at) "
        "VALUES (:id, :p, :id, :id, :parent, :root, :h, :n, :n, :t, :cap, :e, :n, :n)",
        id=ref, p=project, parent=parent, root=root, h=hierarchy, n=stamp, t=prompt,
        cap=stamp if prompt else None, e=expires if prompt else None)
    return ref


async def test_proven_root_required(client, monkeypatch):
    allowed = frozenset({"x"})
    other = "f" * 32
    cases = {
        "1" * 32: ({}, True),
        "2" * 32: ({"root": "2" * 32}, True),
        "3" * 32: ({"root": other}, False),
        "4" * 32: ({"parent": other}, False),
        "5" * 32: ({"hierarchy": "child"}, False),
        "6" * 32: ({"hierarchy": "unknown"}, False),
        "7" * 32: ({"project": "y"}, False),
    }
    for ref, (kwargs, _) in cases.items():
        await _craft(ref, **kwargs)
    async with AsyncSessionLocal() as session:
        for ref, (_, expected) in cases.items():
            assert await task_store.prompt_root_eligible(session, ref, allowed) is expected, ref
        assert await task_store.prompt_root_eligible(session, "9" * 32, allowed) is False

    # through ingest: a root row whose root_task_ref points elsewhere never stores; a clean root does
    _capture(monkeypatch, "x")
    await _post(client, _ev("a", session="r1", turn="r1:t:1", **ROOT))
    await _exec("UPDATE tasks SET root_task_ref = :o WHERE session_id = 'r1'", o=other)
    await _post(client, _ev("b", session="r1", turn="r1:t:1", **ROOT, **_prompt(608)))
    assert await _prompt_of("x", "r1", "r1:t:1") is None
    await _post(client, _ev("c", session="r2", turn="r2:t:1", **ROOT, **_prompt(6081)))
    assert (await _prompt_of("x", "r2", "r2:t:1")).startswith(f"{MARKER}-6081")


@pytest.mark.parametrize("variant", ["parent_fields", "hierarchy_child"])
async def test_prompt_nulled_when_task_becomes_child(client, monkeypatch, outcomes, variant):
    _capture(monkeypatch, "x")
    await _post(client, _ev("p", session="ps", turn="ps:t:1", **ROOT))  # listed parent row
    await _post(client, _ev("a", **ROOT, **_prompt(609)))
    assert (await _prompt_of("x")).startswith(f"{MARKER}-609")
    later = (_ev("b", parent_session_id="ps", parent_turn_id="ps:t:1") if variant == "parent_fields"
             else _ev("b", task_hierarchy="child"))
    await _post(client, later)
    assert outcomes[-1]["prompt"] == "purged_child"
    ((prompt, purged, captured, hierarchy),) = await _rows(
        "SELECT prompt_text, prompt_purged_at, prompt_captured_at, hierarchy_status FROM tasks WHERE session_id = 's1'")
    assert prompt is None and purged is not None and captured is not None and hierarchy == "child"
    await _post(client, _ev("c", **ROOT, **_prompt(6091)))  # never re-stored
    assert await _prompt_of("x") is None
    assert retention.wal_checkpoint(database.DB_PATH)
    _absent(609)
    _absent(6091)

    # JEV afterwards makes no provider call
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    features.refresh()
    gateway = Gateway()
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _run_eval([task_store.task_ref("x", "s1", "s1:t:1")], "r")
    assert gateway.calls == []


async def test_allowlist_change_or_root_metadata_never_purges(client, monkeypatch, outcomes):
    _capture(monkeypatch, "x")
    await _post(client, _ev("a", **ROOT, **_prompt(610)))
    await _post(client, _ev("b", **ROOT))  # a second root metadata event keeps it
    assert (await _prompt_of("x")).startswith(f"{MARKER}-610")
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "z")
    features.refresh()
    await _post(client, _ev("c", **ROOT))  # list removal plus a new root event: no automatic purge
    assert (await _prompt_of("x")).startswith(f"{MARKER}-610")
    assert all(o["prompt"] != "purged_child" for o in outcomes)


async def test_backend_still_accepts_error_message(client):
    response = await _post(client, _ev("a", status="error", error_type="APIError", error_message="old producer text"))
    assert response.status_code == 201
    assert await _rows("SELECT error_message FROM token_events") == [("old producer text",)]


# --- AC8 JEV gate ------------------------------------------------------------------------------------------------


@pytest.fixture
def jev(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "test-key")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "x")
    features.refresh()
    clock = Clock()
    monkeypatch.setattr(jev_scorer, "now", clock.now)
    monkeypatch.setattr(jev_scorer, "sleep", clock.sleep)
    return clock


def _listed(monkeypatch, value):
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", value)
    features.refresh()


A = "a" * 32


async def test_worker_skips_not_allowed_project_without_provider_call(client, jev, monkeypatch):
    _listed(monkeypatch, "z")
    gateway = Gateway()
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _craft(A, prompt="Add tests.")
    await _run_eval([A], "run")
    assert gateway.calls == []
    assert await _rows("SELECT status, error_type FROM task_evaluations") == [("skipped", "project_not_allowed")]
    assert await _rows("SELECT COUNT(*) FROM evaluator_attempts") == [(0,)]


async def test_worker_skips_non_root_tasks(client, jev, monkeypatch):
    gateway = Gateway(ok_from("typesafe-ai"), ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    skipped = [await _craft("1" * 32, hierarchy="child", prompt="p1"),
               await _craft("2" * 32, hierarchy="unknown", prompt="p2"),
               await _craft("3" * 32, root="f" * 32, prompt="p3")]
    scored = [await _craft("4" * 32, prompt="p4"), await _craft("5" * 32, root="5" * 32, prompt="p5")]
    await _run_eval(skipped + scored, "run")
    assert [c["state"] for c in gateway.calls] == ["p4", "p5"]
    status = dict((r[0], r[1:]) for r in await _rows("SELECT task_ref, status, error_type FROM task_evaluations"))
    assert all(status[r] == ("skipped", "project_not_allowed") for r in skipped)
    assert all(status[r] == ("ok", None) for r in scored)


class HookClock(Clock):
    def __init__(self, hook):
        super().__init__()
        self.hook = hook

    async def sleep(self, seconds):
        await super().sleep(seconds)
        if self.hook:
            hook, self.hook = self.hook, None
            await hook()


async def _remove_listing(monkeypatch):
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "z")
    features.refresh()


async def _make_child():
    await _exec("UPDATE tasks SET hierarchy_status = 'child' WHERE id = :id", id=A)


@pytest.mark.parametrize("change", ["remove", "child", None])
async def test_gate_rechecked_before_every_attempt(client, jev, monkeypatch, change):
    hooks = {"remove": lambda: _remove_listing(monkeypatch), "child": _make_child, None: None}
    clock = HookClock(hooks[change])
    monkeypatch.setattr(jev_scorer, "now", clock.now)
    monkeypatch.setattr(jev_scorer, "sleep", clock.sleep)  # the change lands in the backoff wait
    gateway = Gateway((502, {"x": 1}, {}), ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _craft(A, prompt="Add tests.")
    await _run_eval([A], "run")
    if change is None:  # control: the retry happens
        assert len(gateway.calls) == 2
        assert await _rows("SELECT status FROM task_evaluations") == [("ok",)]
        return
    assert len(gateway.calls) == 1
    assert await _rows("SELECT status, error_type FROM task_evaluations") == [("error", "project_not_allowed")]
    assert await _rows("SELECT COUNT(*) FROM evaluator_attempts") == [(1,)]


@pytest.mark.parametrize("change", ["remove", "child"])
async def test_gate_rechecked_before_fallback(client, jev, monkeypatch, change):
    calls = []

    async def gateway(body, api_key):
        calls.append(body["providerOptions"]["gateway"]["only"][0])
        if change == "remove":
            await _remove_listing(monkeypatch)
        else:
            await _make_child()
        status, payload, headers = rate_limited("120")  # a long wait: typesafe-ai falls back at once
        return httpx.Response(status, json=payload, headers=headers)

    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _craft(A, prompt="Add tests.")
    await _run_eval([A], "run")
    assert calls == ["typesafe-ai"]  # no digitalocean call
    assert await _rows("SELECT status, error_type FROM task_evaluations") == [("error", "project_not_allowed")]
    assert await _rows("SELECT COUNT(*) FROM evaluator_attempts") == [(1,)]


async def test_pre_attempt_gate_is_skipped(client, jev, monkeypatch):
    _listed(monkeypatch, "z")
    real = jev_scorer.project_gate
    calls = {"n": 0}

    async def first_open(session, ref):
        calls["n"] += 1
        return None if calls["n"] == 1 else await real(session, ref)

    monkeypatch.setattr(jev_scorer, "project_gate", first_open)
    gateway = Gateway()
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _craft(A, prompt="Add tests.")
    await _run_eval([A], "run")
    assert gateway.calls == [] and calls["n"] >= 2
    assert await _rows("SELECT status, error_type FROM task_evaluations") == [("skipped", "project_not_allowed")]
    assert await _rows("SELECT COUNT(*) FROM evaluator_attempts") == [(0,)]


async def test_removing_project_blocks_stored_tasks_without_purge(client, jev, monkeypatch):
    gateway = Gateway(ok_from("typesafe-ai"))
    monkeypatch.setattr(jev_scorer, "post_evaluation", gateway)
    await _craft(A, prompt="Add tests.")
    first = await client.post("/api/tasks/evaluate", json={"task_refs": [A]}, headers={"X-Ingest-Token": TOKEN})
    assert first.status_code == 202 and len(gateway.calls) == 1
    before = await _rows("SELECT prompt_text, prompt_purged_at FROM tasks WHERE id = :id", id=A)
    _listed(monkeypatch, "z")  # restart equivalent
    body = (await client.post("/api/tasks/evaluate", json={"task_refs": [A]},
                              headers={"X-Ingest-Token": TOKEN})).json()
    assert body["skipped"] == [{"task_ref": A, "reason": "project_not_allowed"}]
    await _run_eval([A], "run2")
    assert await _rows("SELECT status, error_type FROM task_evaluations WHERE run_id = 'run2'") == [
        ("skipped", "project_not_allowed")]
    assert len(gateway.calls) == 1
    assert await _rows("SELECT prompt_text, prompt_purged_at FROM tasks WHERE id = :id", id=A) == before
    assert before[0][0] == "Add tests." and before[0][1] is None


async def test_evaluate_endpoint_reports_project_not_allowed(client, jev, monkeypatch):
    monkeypatch.setattr(jev_scorer, "post_evaluation", Gateway())
    await _craft(A, project="y", prompt="p")
    await _craft("b" * 32, hierarchy="child", prompt="p")
    body = (await client.post("/api/tasks/evaluate", json={"task_refs": [A, "b" * 32]},
                              headers={"X-Ingest-Token": TOKEN})).json()
    assert body["skipped"] == [{"task_ref": A, "reason": "project_not_allowed"},
                               {"task_ref": "b" * 32, "reason": "project_not_allowed"}]
    assert body["run_id"] is None


# --- AC8 Q3: allowed_only ----------------------------------------------------------------------------------------


async def test_tasks_allowed_only_filters_and_needs_auth(client, monkeypatch):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    for ref, project in (("1" * 32, "x"), ("2" * 32, "x"), ("3" * 32, "y")):
        await _craft(ref, project=project)
        await _exec("UPDATE tasks SET first_seen_at = :n, last_seen_at = :n WHERE id = :id", n=now, id=ref)
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "x")
    features.refresh()
    assert (await client.get("/api/tasks?allowed_only=true")).status_code == 403  # auth not configured
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    assert (await client.get("/api/tasks?allowed_only=true")).status_code == 401
    assert (await client.get("/api/tasks?allowed_only=true", headers={"X-Ingest-Token": "w" * 32})).status_code == 401
    body = (await client.get("/api/tasks?allowed_only=true", headers={"X-Ingest-Token": TOKEN})).json()
    assert {i["project_name"] for i in body["items"]} == {"x"} and body["total"] == 2
    assert body["projects"] == ["x", "y"]
    plain = (await client.get("/api/tasks")).json()
    assert plain["total"] == 3
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "")
    features.refresh()
    empty = (await client.get("/api/tasks?allowed_only=true", headers={"X-Ingest-Token": TOKEN})).json()
    assert (empty["items"], empty["total"]) == ([], 0)


# --- AC9 /api/meta and features ----------------------------------------------------------------------------------


async def test_meta_reports_allowlist_state_without_names(client, monkeypatch):
    monkeypatch.delenv("TASK_PROMPT_ALLOWED_PROJECTS", raising=False)
    features.refresh()
    body = (await client.get("/api/meta")).json()
    assert body["task_prompt_allowlist"] == "empty"
    errors = {(e["feature"], e["error"]) for e in body["config_errors"]}
    assert {("capture", "no_allowed_projects"), ("jev", "no_allowed_projects")} <= errors
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "qq-meta-one,qq-meta-two")
    features.refresh()
    response = await client.get("/api/meta")
    assert response.json()["task_prompt_allowlist"] == "configured"
    assert "no_allowed_projects" not in response.text
    assert "qq-meta" not in response.text


async def test_empty_allowlist_disables_capture_with_reason(monkeypatch, caplog):
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.delenv("TASK_PROMPT_ALLOWED_PROJECTS", raising=False)
    caplog.set_level(logging.INFO)
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            body = (await c.get("/api/meta")).json()
    assert (body["task_prompt_capture"], body["task_prompt_capture_disabled_reason"]) == (False, "no_allowed_projects")
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR and "[SECURITY]" in r.getMessage()]
    assert errors == ["[SECURITY] task prompt capture disabled: no_allowed_projects"]
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "x")
    state = features.refresh()
    assert (state.capture_enabled, state.capture_disabled_reason) == (True, None)
    monkeypatch.delenv("INGEST_TOKEN")
    monkeypatch.delenv("TASK_PROMPT_ALLOWED_PROJECTS")
    assert features.refresh().capture_disabled_reason == "ingest_token_missing"  # precedence kept


async def test_jev_disabled_reason_no_allowed_projects(client, monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.delenv("TASK_PROMPT_ALLOWED_PROJECTS", raising=False)
    features.refresh()
    status = (await client.get("/api/tasks/evaluator-status")).json()
    assert (status["enabled"], status["disabled_reason"]) == (False, "no_allowed_projects")
    monkeypatch.delenv("JEV_ENABLED")
    features.refresh()
    assert (await client.get("/api/tasks/evaluator-status")).json()["disabled_reason"] == "not_enabled"
    assert (await client.get("/api/meta")).json()["task_prompt_capture_disabled_reason"] == "not_enabled"


def test_empty_allowlist_contract_by_flags():
    good = {"INGEST_TOKEN": TOKEN, "AI_GATEWAY_API_KEY": "k"}
    off = features.evaluate(good)
    assert (off.capture_disabled_reason, off.jev_disabled_reason, off.allowlist_state) == (
        "not_enabled", "not_enabled", "empty")
    assert {(e["feature"], e["error"]) for e in off.config_errors} == {
        ("capture", "no_allowed_projects"), ("jev", "no_allowed_projects")}
    on = features.evaluate({**good, "STORE_TASK_PROMPTS": "1", "JEV_ENABLED": "1"})
    assert (on.capture_disabled_reason, on.jev_disabled_reason) == ("no_allowed_projects", "no_allowed_projects")
    no_token = features.evaluate({"AI_GATEWAY_API_KEY": "k", "STORE_TASK_PROMPTS": "1", "JEV_ENABLED": "1"})
    assert (no_token.capture_disabled_reason, no_token.jev_disabled_reason) == (
        "ingest_token_missing", "ingest_token_missing")
    listed = features.evaluate({**good, "TASK_PROMPT_ALLOWED_PROJECTS": "x"})
    assert listed.config_errors == [] and listed.allowlist_state == "configured"


async def test_allowlist_config_boundary(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("STORE_TASK_PROMPTS", "1")
    monkeypatch.setenv("JEV_ENABLED", "1")
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "k")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    monkeypatch.setenv("TASK_PROMPT_ALLOWED_PROJECTS", "zz-alpha-proj,zz-beta-proj,bad entry!")
    async with lifespan(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            await _exec("DELETE FROM task_evaluations")
            await _exec("DELETE FROM tasks")
            await _exec("DELETE FROM token_events")
            meta = await c.get("/api/meta")
            event = _ev("a", **ROOT, **_prompt(611), tags={"k": "v"})
            assert (await c.post("/api/events", json=event, headers=_headers("zz-alpha-proj"))).status_code == 201
            ref = task_store.task_ref("zz-alpha-proj", "s1", "s1:t:1")
            monkeypatch.setattr(jev_scorer, "post_evaluation", Gateway())
            await _run_eval([ref], "run")
            listed = (await c.get("/api/tasks?allowed_only=true", headers={"X-Ingest-Token": TOKEN})).json()
            tags = await _rows("SELECT project_name, tags_json FROM token_events WHERE role IS NULL")
            await _exec("DELETE FROM task_evaluations")
            await _exec("DELETE FROM evaluator_attempts")
            await _exec("DELETE FROM tasks")
            await _exec("DELETE FROM token_events")
    features.refresh()
    for name in ("zz-alpha-proj", "zz-beta-proj"):
        assert name not in meta.text
    assert not any(v == 2 or v == 3 for v in meta.json().values() if isinstance(v, int) and not isinstance(v, bool)
                   ) and meta.json()["schema_version"] == 10
    # driver DEBUG records echo SQL parameters (the project name of a normal event, which is permitted)
    app_log = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith(("aiosqlite", "sqlalchemy")))
    warnings = [r.getMessage() for r in caplog.records if r.name == "features" and r.levelno == logging.WARNING]
    assert warnings and set(warnings) == {"invalid allowlist entries ignored"}  # no count, no names
    assert "zz-alpha" not in app_log and "zz-beta" not in app_log and "bad entry" not in app_log
    assert tags == [("zz-alpha-proj", '{"k":"v"}')]  # permitted: the project name of normal telemetry
    assert [i["task_ref"] for i in listed["items"]] == [ref]  # permitted: authenticated allowed_only


def test_invalid_entries_dropped_without_count(caplog):
    caplog.set_level(logging.DEBUG)
    state = features.evaluate({"TASK_PROMPT_ALLOWED_PROJECTS": "X, bad name!, also bad!"})
    assert state.allowed_projects == frozenset({"x"})
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ["invalid allowlist entries ignored"]
    assert not any(ch.isdigit() for ch in caplog.text.split("invalid allowlist")[1])
    assert "bad name" not in caplog.text and "also bad" not in caplog.text
    caplog.clear()
    assert features.evaluate({"TASK_PROMPT_ALLOWED_PROJECTS": "x, y.z, a-b_c"}).allowed_projects == frozenset(
        {"x", "y.z", "a-b_c"})
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    garbage = features.evaluate({"TASK_PROMPT_ALLOWED_PROJECTS": "!!!, pega/../x, ,"})
    assert garbage.allowed_projects == frozenset() and "hermes" not in garbage.allowed_projects
