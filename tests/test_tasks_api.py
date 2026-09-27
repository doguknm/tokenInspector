"""Tasks API: list/detail/labels (AC7a, AC7m, AC5c, AC8b label storage, AC11a on sensitive routes)."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

import task_store
from database import AsyncSessionLocal

TOKEN = "k" * 24
H = {"X-Project-Name": "hermes"}


def _iso(delta_minutes: float = 0) -> str:
    value = datetime.now(timezone.utc) + timedelta(minutes=delta_minutes)
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def _ingest(client, cid, turn, session="s1", minutes=-5, **extra):
    body = {"client_event_id": cid, "model": "claude-sonnet-4-6", "session_id": session, "turn_id": turn,
            "task_id": "gw", "occurred_at": _iso(minutes), "prompt_tokens": 100, "completion_tokens": 10}
    body.update(extra)
    response = await client.post("/api/events", json=body, headers=H)
    assert response.status_code == 201, response.text
    return task_store.task_ref("hermes", session, turn)


async def _sql(statement, **params):
    async with AsyncSessionLocal() as session:
        await session.execute(text(statement), params)
        await session.commit()


async def _jev(ref, raw, evaluated_at="2026-09-27T00:00:00.000000Z"):
    await _sql(
        "INSERT INTO task_evaluations (id, task_ref, evaluator, rubric_version, status, raw_score, confidence, "
        "probabilities_json, legend_json, provider_used, http_attempts, evaluated_at) VALUES (:id, :t, 'jev', "
        "'difficulty-v0', 'ok', :raw, 0.8, '[0,0,0.8,0.2,0]', '[\"a\",\"b\",\"c\",\"d\",\"e\"]', 'typesafe-ai', 1, :at)",
        id=f"jev-{ref}-{raw}", t=ref, raw=raw, at=evaluated_at,
    )


async def test_list_shape_costs_and_no_prompt_text(client):
    priced = await _ingest(client, "a", "s1:t:1")
    unpriced = await _ingest(client, "b", "s1:t:2", minutes=-4, model="no-such-model")
    await _ingest(client, "c", "s1:t:1", minutes=-3, event_type="tool_call")
    await _sql("UPDATE tasks SET prompt_text = 'SECRET-PROMPT', prompt_captured_at = :n, prompt_expires_at = :e",
               n=_iso(), e=_iso(60 * 24))
    body = (await client.get("/api/tasks")).json()
    assert set(body) == {"items", "total", "page", "page_size", "projects"}
    assert "SECRET-PROMPT" not in json.dumps(body)
    items = {i["task_ref"]: i for i in body["items"]}
    assert items[priced]["cost_usd"] > 0 and items[priced]["tool_call_count"] == 1
    assert items[priced]["llm_request_count"] == 1 and items[priced]["total_tokens"] == 110
    assert items[unpriced]["cost_usd"] is None and items[unpriced]["unpriced_count"] == 1
    assert items[priced]["prompt_state"] == "retained" and items[priced]["has_prompt"] is True
    assert items[priced]["turn_id"] == "s1:t:1" and items[priced]["source_task_id"] == "gw"


async def test_stable_order_and_out_of_range_page(client):
    refs = [await _ingest(client, f"e{i}", f"s{i}:t:1", session=f"s{i}", minutes=-10) for i in range(3)]
    await _sql("UPDATE tasks SET last_seen_at = '2026-09-27T09:00:00.000000Z'")
    page1 = (await client.get("/api/tasks?page_size=2&days=3650")).json()
    page2 = (await client.get("/api/tasks?page_size=2&page=2&days=3650")).json()
    assert [i["task_ref"] for i in page1["items"] + page2["items"]] == sorted(refs)
    beyond = (await client.get("/api/tasks?page=9&days=3650")).json()
    assert beyond["items"] == [] and beyond["total"] == 3
    assert (await client.get("/api/tasks?page=0")).status_code == 422
    assert (await client.get("/api/tasks?page_size=201")).status_code == 422


async def test_completion_inferred_or_open_and_prompt_states(client):
    old = await _ingest(client, "a", "s1:t:1", session="s1", minutes=-120)
    fresh = await _ingest(client, "b", "s2:t:1", session="s2", minutes=-1)
    expired = await _ingest(client, "c", "s3:t:1", session="s3", minutes=-1)
    purged = await _ingest(client, "d", "s4:t:1", session="s4", minutes=-1)
    await _sql("UPDATE tasks SET prompt_text = 'x', prompt_captured_at = :c, prompt_expires_at = :e WHERE id = :id",
               c=_iso(-60 * 24 * 31), e=_iso(-5), id=expired)
    await _sql("UPDATE tasks SET prompt_purged_at = :n WHERE id = :id", n=_iso(), id=purged)
    items = {i["task_ref"]: i for i in (await client.get("/api/tasks")).json()["items"]}
    assert items[old]["completion"] == "inferred" and items[old]["completed_at"] == items[old]["last_seen_at"]
    assert (items[fresh]["completion"], items[fresh]["completed_at"]) == ("open", None)
    assert [items[r]["prompt_state"] for r in (fresh, expired, purged)] == ["none", "expired", "purged"]
    assert items[expired]["has_prompt"] is False


async def test_filters_and_projects_ignore_everything_but_days(client):
    a = await _ingest(client, "a", "s1:t:1", task_hierarchy="root")
    await client.post("/api/events", json={"client_event_id": "z", "model": "m", "session_id": "q", "turn_id": "q:t:1",
                                           "occurred_at": _iso(-5)}, headers={"X-Project-Name": "other"})
    await _jev(a, 2.2)
    assert [i["task_ref"] for i in (await client.get("/api/tasks?evaluated=true")).json()["items"]] == [a]
    assert [i["task_ref"] for i in (await client.get("/api/tasks?root_only=true")).json()["items"]] == [a]
    only_hermes = (await client.get("/api/tasks?project=hermes&evaluated=true&root_only=true")).json()
    assert only_hermes["projects"] == ["hermes", "other"]
    await _sql("UPDATE tasks SET last_seen_at = '2020-01-01T00:00:00.000000Z' WHERE project_name = 'other'")
    assert (await client.get("/api/tasks?days=30")).json()["projects"] == ["hermes"]
    item = (await client.get("/api/tasks?evaluated=true")).json()["items"][0]
    assert item["jev"]["raw_score"] == 2.2 and item["jev"]["display_score"] == 3.2
    assert item["jev"]["probabilities"] == [0, 0, 0.8, 0.2, 0]


async def test_detail_children_and_include_prompt_auth(client, monkeypatch):
    parent = await _ingest(client, "p", "s1:t:1")
    child = await _ingest(client, "c", "c1:t:1", session="c1", parent_session_id="s1", parent_turn_id="s1:t:1",
                          complexity=2, complexity_method="request-shape-v1")
    await _jev(child, 1.4)
    await _sql("UPDATE tasks SET prompt_text = 'RETAINED', prompt_captured_at = :c, prompt_expires_at = :e "
               "WHERE id = :id", c=_iso(), e=_iso(60), id=parent)
    detail = (await client.get(f"/api/tasks/{parent}")).json()
    assert "prompt_text" not in detail
    assert detail["child_count"] == 1 and detail["children_truncated"] is False
    assert detail["children"] == [{"task_ref": child, "turn_id": "c1:t:1", "source_task_id": "gw",
                                   "hierarchy_status": "child", "start_complexity": 2, "jev_raw_score": 1.4,
                                   "first_seen_at": detail["children"][0]["first_seen_at"]}]
    monkeypatch.delenv("INGEST_TOKEN", raising=False)
    assert (await client.get(f"/api/tasks/{parent}?include_prompt=true")).status_code == 403
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    assert (await client.get(f"/api/tasks/{parent}?include_prompt=true")).status_code == 401
    ok = await client.get(f"/api/tasks/{parent}?include_prompt=true", headers={"X-Ingest-Token": TOKEN})
    assert ok.json()["prompt_text"] == "RETAINED"
    await _sql("UPDATE tasks SET prompt_expires_at = :e WHERE id = :id", e=_iso(-1), id=parent)
    expired = await client.get(f"/api/tasks/{parent}?include_prompt=true", headers={"X-Ingest-Token": TOKEN})
    assert expired.json()["prompt_text"] is None  # read-time enforcement before any purge
    assert (await client.get("/api/tasks/" + "0" * 32)).status_code == 404
    assert (await client.get("/api/tasks/not-a-ref")).status_code == 404


async def test_labels_upsert_skip_and_scrubbed_note_never_returned(client, monkeypatch):
    ref = await _ingest(client, "a", "s1:t:1")
    monkeypatch.setenv("INGEST_TOKEN", TOKEN)
    auth = {"X-Ingest-Token": TOKEN}
    url = f"/api/tasks/{ref}/labels"
    first = await client.post(url, json={"label": 3, "labeler": "dogukan", "note": "see sk-" + "z" * 20}, headers=auth)
    assert first.status_code == 201 and first.json()["status"] == "ok"
    again = await client.post(url, json={"skipped": True, "labeler": "dogukan"}, headers=auth)
    assert again.status_code == 200 and again.json()["status"] == "skipped" and again.json()["id"] == first.json()["id"]
    await client.post(url, json={"label": 1, "labeler": "dogukan", "note": "see sk-" + "z" * 20}, headers=auth)
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text("SELECT status, label, note FROM task_evaluations"))).all()
    assert rows == [("ok", 1, "see [REDACTED:secret]")]
    detail = (await client.get(f"/api/tasks/{ref}")).json()
    assert detail["human_label_count"] == 1 and "note" not in json.dumps(detail["evaluations"])
    for bad in ({"labeler": "dogukan"}, {"label": 2, "skipped": True, "labeler": "dogukan"},
                {"label": 5, "labeler": "dogukan"}, {"label": 1, "labeler": "Bad Name"},
                {"label": 1, "labeler": "x", "note": "n" * 281}):
        assert (await client.post(url, json=bad, headers=auth)).status_code == 422, bad
    assert (await client.post(url, json={"label": 1, "labeler": "x"})).status_code == 401
    assert (await client.post(f"/api/tasks/{'0' * 32}/labels", json={"label": 1, "labeler": "x"},
                              headers=auth)).status_code == 404
