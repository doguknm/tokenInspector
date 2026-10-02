"""O9 Phase 3: export keyset pagination over a snapshot (ingest_seq high-water, revision/epoch) — AC3.4."""

import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel.ext.asyncio.session import AsyncSession

import database
from routes import export as export_module
from scripts import repair_task_parents, rollback_schema
from test_export_contract import END, PLUGIN, START, ev, export, post, walk
from test_job_correlation import JOB_A, JOB_B, rows
from test_repair_task_parents import COLS, REF, wrong

needs_drop_column = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 35, 0), reason="DROP COLUMN needs SQLite 3.35+")


def _at(i: int) -> str:
    return f"2026-09-{1 + i % 27:02d}T{i % 24:02d}:{i % 60:02d}:00Z"


async def test_pages_concatenate_to_full_result(client):
    jobs = ("20260901-000000-1", "20260901-000000-2", "20260901-000000-3", "devir-20260901-000000-4")
    bodies = [ev(f"e{i:04d}", at=_at(i), session=f"s{i % 37}", turn=f"t{i % 11}",
                 tags={**PLUGIN, "job_ref": jobs[i % 4]}) for i in range(1234)]
    for chunk in range(0, len(bodies), 400):
        await post(client, *bodies[chunk:chunk + 400])
    expected_events = [r[0] for r in await rows(
        "SELECT id FROM token_events WHERE COALESCE(occurred_at, recorded_at) >= '2026-09-01' ORDER BY ingest_seq")]
    for dataset, key in (("events", "event_id"), ("tasks", "task_ref"), ("jobs", "job_ref")):
        small, pages = await walk(client, dataset, limit=100)
        big, _ = await walk(client, dataset, limit=1000)
        keys = [i[key] for i in small]
        assert keys == [i[key] for i in big] and len(keys) == len(set(keys)), dataset
        if dataset == "events":
            assert keys == expected_events and len(keys) == 1234 and pages == 13
        if dataset == "jobs":
            assert keys == sorted(jobs)
    tasks, pages = await walk(client, "tasks", limit=7)
    assert pages > 1 and len(tasks) == len(await rows("SELECT id FROM tasks"))


async def test_pagination_under_concurrent_ingest(client):
    await post(client, *(ev(f"a{i}", turn=f"t{i}", at=_at(i)) for i in range(5)))
    page1 = await export(client, "events", limit=2)
    tasks1 = await export(client, "tasks", limit=2)
    page1_refs = {task["task_ref"] for task in tasks1["items"]}
    target_ref, target_turn = next((task_ref, turn) for task_ref, turn in await rows("SELECT id, turn_id FROM tasks")
                                   if task_ref not in page1_refs)
    await post(client,
               ev("new-in-period", turn="n1", at="2026-09-20T00:00:00Z"),        # (a) new event in the period
               ev("late-old-time", turn="n2", at="2026-09-01T00:00:01Z"),        # (b) late event, old occurred_at
               ev("same-task", turn=target_turn, at="2026-09-02T00:00:00Z", prompt_tokens=1000))  # (c) later page
    rest = await walk_from(client, "events", page1)
    ids = [i["client_event_id"] for i in page1["items"] + rest]
    assert sorted(ids) == sorted(f"a{i}" for i in range(5))
    task_rest = await walk_from(client, "tasks", tasks1)
    assert sum(t["prompt_tokens"] for t in tasks1["items"] + task_rest) == 50  # sums unchanged within the chain
    target = next(task for task in task_rest if task["task_ref"] == target_ref)
    assert (target["llm_request_count"], target["prompt_tokens"], target["completion_tokens"]) == (1, 10, 5)
    fresh, _ = await walk(client, "events")
    assert {"new-in-period", "late-old-time", "same-task"} <= {i["client_event_id"] for i in fresh}


async def walk_from(client, dataset, page):
    items = []
    while not page["complete"]:
        page = await export(client, dataset, limit=2, cursor=page["next_cursor"])
        items += page["items"]
    return items


async def test_snapshot_job_membership_is_frozen(client):
    job0 = "20260901-000000-1"  # sorts before JOB_A and JOB_B: alone on page 1
    # task T (s-t) has only untagged calls when page 1 is read; job A has one other task
    await post(client, ev("z1", session="s-z", turn="tz", tags={**PLUGIN, "job_ref": job0}),
               ev("a1", session="s-a", turn="ta", tags={**PLUGIN, "job_ref": JOB_A}),
               ev("t1", session="s-t", turn="tt"),
               ev("b1", session="s-b", turn="tb", tags={**PLUGIN, "job_ref": JOB_B}))
    page1 = await export(client, "jobs", limit=1)
    assert [j["job_ref"] for j in page1["items"]] == [job0]
    events1 = await export(client, "events", limit=1)
    await post(client, ev("t2", session="s-t", turn="tt", tags={**PLUGIN, "job_ref": JOB_A}),  # T joins A
               ev("b2", session="s-b", turn="tb", tags={**PLUGIN, "job_ref": JOB_A}))  # conflicting job on B's task
    assert await rows("SELECT job_ref FROM tasks WHERE session_id = 's-t'") == [(JOB_A,)]  # the live column moved
    rest = {j["job_ref"]: j for j in await walk_from(client, "jobs", page1)}
    assert set(rest) == {JOB_A, JOB_B}
    assert rest[JOB_A]["task_count"] == 1 and rest[JOB_A]["llm_request_count"] == 1  # T stays out of A
    assert rest[JOB_B]["conflict_count"] == 0 and rest[JOB_B]["llm_request_count"] == 1
    t1 = next(i for i in [*events1["items"], *await walk_from(client, "events", events1)] if i["client_event_id"] == "t1")
    assert "job_ref" not in t1  # in this snapshot T has no job
    fresh = {j["job_ref"]: j for j in (await walk(client, "jobs"))[0]}
    assert fresh[JOB_A]["task_count"] == 2 and fresh[JOB_A]["llm_request_count"] == 3  # T with both calls
    assert fresh[JOB_B]["conflict_count"] == 1 and fresh[JOB_B]["conflict_task_count"] == 1


async def test_cursor_expires_on_mutation(client, tmp_path, capsys):
    await post(client, *(ev(f"e{i}", turn=f"t{i}", model="zz-mutation-model") for i in range(3)))
    expired = {"error": "snapshot_expired", "schema_version": 1}

    async def page2(page1):
        return await client.get("/api/export/v1/events", params={"from": START, "to": END, "limit": 1,
                                                                 "cursor": page1["next_cursor"]})

    page1 = await export(client, "events", limit=1)
    assert (await client.post("/api/settings/recost", params={"dry_run": "true"})).json()["changed"] == 0
    assert (await client.post("/api/settings/recost", params={"dry_run": "false"})).json()["changed"] == 0
    await post(client, ev("new", turn="n"))  # new ingest
    assert (await page2(page1)).status_code == 200
    rule = {"model": "zz-mutation-model", "input_price_per_1m": 1.0, "output_price_per_1m": 1.0}
    assert (await client.post("/api/settings/pricing", json=rule)).status_code == 200
    assert (await client.post("/api/settings/recost", params={"dry_run": "true"})).json()["changed"] == 3
    assert (await page2(page1)).status_code == 200  # dry-run changes nothing
    assert (await client.post("/api/settings/recost", params={"dry_run": "false"})).json()["changed"] == 3
    response = await page2(page1)
    assert response.status_code == 409 and response.json() == expired
    assert (await page2(await export(client, "events", limit=1))).status_code == 200  # restart from page 1 works

    # repair --apply with a change on the live DB expires cursors too
    page1 = await export(client, "events", limit=1)
    async with database.AsyncSessionLocal() as session:
        await session.execute(text(f"INSERT INTO tasks ({COLS}) VALUES (:a, 'a', 'ps', 'pt', NULL, NULL, "
                                            "'root', 'ingest', 'x', 'x', 0, 'x', 'x')"), {"a": REF("a", "ps", "pt")})
        await session.execute(text(f"INSERT INTO tasks ({COLS}) VALUES (:c, 'b', 'cs', 'ct', :w, :w, "
                                            "'child', 'ingest', 'x', 'x', 0, 'x', 'x')"),
                              {"c": REF("b", "cs", "ct"), "w": wrong("b", "ps", "pt")})
        await session.commit()
    assert repair_task_parents.main(["--db", database.DB_PATH]) == 0  # dry-run
    assert (await page2(page1)).status_code == 200
    assert repair_task_parents.main(["--db", database.DB_PATH, "--apply", "--backup-dir", str(tmp_path)]) == 0
    assert (await page2(page1)).json() == expired
    capsys.readouterr()


# --- AC-DB3.6 / AC-DB3.3: cursors across old-code writes, deletes, rollback and re-upgrade ----------------------


async def _export_db(path, dataset, **params):
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with AsyncSession(engine) as session:
            return await export_module._export(dataset, START, END, params.get("limit"), params.get("cursor"), session)
    finally:
        await engine.dispose()


async def _walk_db(path, page):
    items = list(page["items"])
    while not page["complete"]:
        page = await _export_db(path, "events", limit="2", cursor=page["next_cursor"])
        assert isinstance(page, dict), page
        items += page["items"]
    return [i["event_id"] for i in items]


def _old_insert(conn, event_id, at, client_event_id=None, project="hermes"):
    """An INSERT shaped like v10/v11 code: no ingest_seq."""
    conn.execute("INSERT OR IGNORE INTO token_events (id, project_name, client_event_id, recorded_at, occurred_at, "
                 "event_type, model, prompt_tokens, completion_tokens, cache_read_tokens, cache_creation_tokens, "
                 "reasoning_tokens, status, attempt, retry_count, cost_status) VALUES (?, ?, ?, ?, ?, 'llm_request', "
                 "'m', 0, 0, 0, 0, 0, 'success', 1, 0, 'unpriced')", (event_id, project, client_event_id, at, at))


@needs_drop_column
async def test_v10_v12_old_code_round_trip(tmp_path):
    path = tmp_path / "db.sqlite"
    await database.init_db(str(path))
    assert rollback_schema.main(["--db", str(path), "--to", "10"]) == 0
    with closing(sqlite3.connect(path)) as conn:  # v10 data
        for i in range(4):
            _old_insert(conn, f"v10-{i}", f"2026-09-0{i + 1}T00:00:00.000000Z", f"c{i}")
        conn.commit()
    await database.init_db(str(path))  # v12 code migrates 10 -> 12
    page1 = await _export_db(path, "events", limit="2")
    before = await _walk_db(path, page1)
    assert before == [f"v10-{i}" for i in range(4)]
    with closing(sqlite3.connect(path)) as conn:
        seqs = dict(conn.execute("SELECT id, ingest_seq FROM token_events"))
    with closing(sqlite3.connect(path)) as conn:  # v11-shaped code after a code-only rollback
        _old_insert(conn, "old-new", "2026-09-02T12:00:00.000000Z", "c-new")
        _old_insert(conn, "old-dup", "2026-09-02T12:00:00.000000Z", "c1")  # duplicate client_event_id
        conn.commit()
    await database.init_db(str(path))  # v12 code restarts: heal above the high-water
    with closing(sqlite3.connect(path)) as conn:
        now = dict(conn.execute("SELECT id, ingest_seq FROM token_events"))
    assert {k: now[k] for k in seqs} == seqs and "old-dup" not in now
    assert now["old-new"] == max(seqs.values()) + 1
    assert await _walk_db(path, page1) == before  # the saved cursor still returns the same pages
    fresh = await _walk_db(path, await _export_db(path, "events", limit="2"))
    assert "old-new" in fresh
    assert rollback_schema.main(["--db", str(path), "--to", "11"]) == 0  # schema rollback, then re-upgrade
    await database.init_db(str(path))
    response = await _export_db(path, "events", limit="2", cursor=page1["next_cursor"])
    assert response.status_code == 409  # epoch changed: the old cursor is refused


async def test_cursor_ignores_rows_after_delete_and_reingest(client):
    await post(client, *(ev(f"d{i}", turn=f"t{i}") for i in range(4)))
    page1 = await export(client, "events", limit=1)
    async with database.AsyncSessionLocal() as session:  # maintenance deletes the highest rows
        await session.execute(text(
            "DELETE FROM token_events WHERE ingest_seq > (SELECT last_seq - 2 FROM export_state)"))
        await session.commit()
    await post(client, ev("after-1", turn="x1"), ev("after-2", turn="x2"))
    rest = await walk_from(client, "events", page1)
    assert not {"after-1", "after-2"} & {i["client_event_id"] for i in rest}
