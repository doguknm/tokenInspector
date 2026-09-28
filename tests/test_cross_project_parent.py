"""O9 Phase 1: cross-project child hierarchy via parent_project_name (J6, AC1.7)."""

import pytest
from sqlalchemy import text

import task_store
from database import AsyncSessionLocal
from test_job_correlation import event, post, rows

REF = task_store.task_ref


async def _task(project, session, turn):
    (row,) = await rows("SELECT parent_task_ref, root_task_ref, hierarchy_status, prompt_text FROM tasks WHERE id = :id",
                        id=REF(project, session, turn))
    return row


def child_event(cid, parent_project, **extra):
    body = event(cid, session="cs", turn="ct", parent_session_id="ps", parent_turn_id="pt", task_hierarchy="child",
                 **extra)
    if parent_project is not None:
        body["parent_project_name"] = parent_project
    return body


@pytest.mark.parametrize("given", ["a", " A "])
async def test_cross_project_child_parent_ref(client, given):
    await post(client, event("p1", session="ps", turn="pt", task_hierarchy="root"), project="a")
    await post(client, child_event("c1", given), project="b")
    parent_ref, root_ref, hierarchy, _ = await _task("b", "cs", "ct")
    assert parent_ref == REF("a", "ps", "pt")
    assert root_ref == REF("a", "ps", "pt") and hierarchy == "child"
    stored = await rows("SELECT tags_json FROM token_events WHERE client_event_id = 'c1'")
    assert "parent_project_name" not in str(stored)  # validated, used, never stored


async def _grandparent_chain(client, order):
    """root R (project a) -> parent P (project a, child of R) -> child C (project b, child of P)."""
    root = event("r", session="rs", turn="rt", task_hierarchy="root")
    parent = event("p", session="ps", turn="pt", parent_session_id="rs", parent_turn_id="rt", task_hierarchy="child")
    child = child_event("c", "a")
    steps = {"parent_first": [(root, "a"), (parent, "a"), (child, "b")],
             "child_first": [(root, "a"), (child, "b"), (parent, "a")]}[order]
    for body, project in steps:
        await post(client, body, project=project)
    return await _task("b", "cs", "ct"), await _task("a", "ps", "pt")


async def test_cross_project_child_either_order(client):
    first = await _grandparent_chain(client, "parent_first")
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM tasks"))
        await session.execute(text("DELETE FROM token_events"))
        await session.commit()
    second = await _grandparent_chain(client, "child_first")
    assert first == second
    child, parent = second
    assert child[0] == REF("a", "ps", "pt")
    assert child[1] == REF("a", "rs", "rt") == parent[1]  # re-rooted across projects


@pytest.mark.parametrize("value", [None, "", "Bad Name!", "../x", 123, {"a": 1}, "-x", "x" * 65])
async def test_invalid_parent_project_name_keeps_today_behaviour(client, value):
    body = child_event("c1", None)
    if value is not None:
        body["parent_project_name"] = value
    single = await client.post("/api/events", json=body, headers={"X-Project-Name": "b"})
    assert single.status_code == 201, single.text
    parent_ref, root_ref, hierarchy, _ = await _task("b", "cs", "ct")
    assert parent_ref == REF("b", "ps", "pt") == root_ref and hierarchy == "child"


async def test_child_never_downgraded_cross_project(client):
    await post(client, event("p1", session="ps", turn="pt", task_hierarchy="root"), project="a")
    await post(client, child_event("c1", "a"), project="b")
    await post(client, event("c2", session="cs", turn="ct", task_hierarchy="root",
                              task_prompt_text="ZZ-CANARY prompt", prompt_eligibility="v1-allowed"), project="b")
    parent_ref, _root, hierarchy, prompt = await _task("b", "cs", "ct")
    assert hierarchy == "child" and parent_ref == REF("a", "ps", "pt") and prompt is None
    async_rows = await rows("SELECT COUNT(*) FROM tasks WHERE prompt_text IS NOT NULL")
    assert async_rows == [(0,)]
