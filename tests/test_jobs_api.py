"""O9 Phase 1: GET /api/jobs, cost per launcher job (J5, AC1.5)."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from database import AsyncSessionLocal
from test_job_correlation import JOB_A, JOB_B, JOB_C, event, post

PRICED, UNPRICED = "claude-sonnet-4-6", "zz-unpriced-model"
CC = {"runtime": "claude-code@hermes", "producer": "claude-code-hook"}


def ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


async def jobs(client, **params):
    response = await client.get("/api/jobs", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_ref(data):
    return {item["job_ref"]: item for item in data["items"]}


async def test_jobs_api_one_row_per_job(client):
    await post(client,
               event("a1", session="s1", turn="t1", job=JOB_A, at=ago(5), tags={"work_type": "review", "job_attempt": 1}),
               event("a2", session="s1", turn="t1", job=JOB_A, at=ago(4.5), tags={"work_type": "review", "job_attempt": 1},
                     cache_read_tokens=7, cache_creation_tokens=3),
               event("a3", session="c1", turn="ct", job=JOB_A, at=ago(4), tags={"work_type": "review", "job_attempt": 1},
                     parent_session_id="s1", parent_turn_id="t1", task_hierarchy="child"),
               event("a4", session="s1", turn="t2", job=JOB_A, at=ago(3), tags={"work_type": "review", "job_attempt": 2}),
               event("b1", session="s2", turn="t1", job=JOB_B, at=ago(2), tags={"work_type": "code"}),
               event("n1", session="s3", turn="t1", at=ago(1)),
               event("n2", session=None, turn=None, at=ago(1)))
    await post(client, event("d1", session="s9", turn="t1", job=JOB_C, at=ago(6), tags=CC), project="pegadocrag")
    data = await jobs(client)
    assert [i["job_ref"] for i in data["items"]] == [JOB_B, JOB_A, JOB_C]  # last_event_at DESC
    assert data["total"] == 3
    a = by_ref(data)[JOB_A]
    assert (a["task_count"], a["llm_request_count"]) == (3, 4)
    assert (a["prompt_tokens"], a["completion_tokens"], a["cache_read_tokens"], a["cache_creation_tokens"]) == (40, 20, 7, 3)
    assert (a["runtimes"], a["work_type"], a["work_types"], a["attempts"], a["projects"]) == (
        ["hermes-agent"], "review", ["review"], [1, 2], ["hermes"])
    assert a["first_event_at"] < a["last_event_at"]
    c = by_ref(data)[JOB_C]
    assert (c["runtimes"], c["projects"], c["work_type"], c["attempts"]) == (["claude-code@hermes"], ["pegadocrag"], None, [])


async def test_job_cost_semantics(client):
    jobs_ = {"20260928-000001-1": [PRICED, PRICED], "20260928-000002-2": [PRICED, UNPRICED],
             "20260928-000003-3": [UNPRICED, UNPRICED]}
    bodies = [event(f"{ref}-{n}", session=ref, turn="t", job=ref, model=model, at=ago(1))
              for ref, models in jobs_.items() for n, model in enumerate(models)]
    # (d) an estimated call: total_tokens only on a priced model
    bodies.append(event("est-1", session="est", turn="t", job="20260928-000004-4", at=ago(1), prompt_tokens=0,
                        completion_tokens=0, total_tokens=1000))
    bodies.append(event("est-2", session="est", turn="t", job="20260928-000004-4", at=ago(1)))
    await post(client, *bodies)
    data = by_ref(await jobs(client))
    a, b, c, d = (data[r] for r in ("20260928-000001-1", "20260928-000002-2", "20260928-000003-3", "20260928-000004-4"))
    assert a["cost_usd"] == pytest.approx(2 * (10 * 3 + 5 * 15) / 1e6) and a["cost_complete"] is True
    assert (a["priced_count"], a["unpriced_count"], a["estimated_cost_usd"]) == (2, 0, 0.0)
    assert b["cost_usd"] == pytest.approx((10 * 3 + 5 * 15) / 1e6)
    assert (b["unpriced_count"], b["cost_complete"]) == (1, False)
    assert c["cost_usd"] is None and c["cost_complete"] is False and c["unpriced_count"] == 2
    assert d["estimated_cost_usd"] > 0 and d["cost_complete"] is False and d["unpriced_count"] == 0
    assert d["cost_usd"] == pytest.approx((10 * 3 + 5 * 15) / 1e6)


async def test_task_belongs_to_one_job(client):
    await post(client, event("e1", job=JOB_A, at=ago(3)), event("e2", job=JOB_B, at=ago(2)))
    data = await jobs(client)
    assert list(by_ref(data)) == [JOB_A]  # B owns no task: the conflicting call counts under A
    a = by_ref(data)[JOB_A]
    assert (a["llm_request_count"], a["conflict_count"], a["conflict_task_count"]) == (2, 1, 1)
    assert data["anomalies"]["job_ref_conflicts"] == 1


async def test_jobs_exclude_evaluator(client):
    await post(client, event("e1", job=JOB_A, at=ago(1)))
    evaluator = event("jev-1", session=None, turn=None, job=JOB_A, at=ago(1), role="evaluator",
                      tags={"purpose": "evaluator"})
    await post(client, evaluator, project="token-inspector")
    a = by_ref(await jobs(client))[JOB_A]
    assert (a["llm_request_count"], a["prompt_tokens"], a["projects"]) == (1, 10, ["hermes"])


async def test_jobs_filters_and_window(client):
    await post(client,
               event("a1", session="a", turn="t", job=JOB_A, at=ago(2), tags={"work_type": "review"}),
               event("b1", session="b", turn="t", job=JOB_B, at=ago(24 * 10), tags={"work_type": "code"}))
    await post(client, event("c1", session="c", turn="t", job=JOB_C, at=ago(1), tags=CC | {"work_type": "devir"}),
               project="pegadocrag")
    assert list(by_ref(await jobs(client, days=7))) == [JOB_C, JOB_A]
    assert list(by_ref(await jobs(client, days=30))) == [JOB_C, JOB_A, JOB_B]
    only_cc = await jobs(client, runtime="claude-code@hermes")
    assert list(by_ref(only_cc)) == [JOB_C] and only_cc["total"] == 1
    assert only_cc["filters"]["runtimes"] == ["claude-code@hermes", "hermes-agent"]  # ignores its own filter
    assert only_cc["filters"]["work_types"] == ["devir"]
    code = await jobs(client, work_type="code")
    assert list(by_ref(code)) == [JOB_B]
    assert code["filters"]["work_types"] == ["code", "devir", "review"]
    for params in ({"runtime": "zz-canary-host.internal"}, {"work_type": "Bad Value"}):
        bad = await client.get("/api/jobs", params=params)
        assert bad.status_code == 400 and bad.json() == {"error": "invalid_filter"}
        assert "canary" not in bad.text and "Bad" not in bad.text
    paged = await jobs(client, days=30, page=2, page_size=2)
    assert [i["job_ref"] for i in paged["items"]] == [JOB_B] and paged["total"] == 3


async def test_jobs_filters_select_whole_jobs(client):
    mixed = "20260928-140000-555"
    await post(client, event("m1", session="m1", turn="t", job=mixed, at=ago(2), tags={"work_type": "review"}),
               event("s1", session="s1", turn="t", job=JOB_A, at=ago(2), tags={"work_type": "review"}))
    await post(client, event("m2", session="m2", turn="t", job=mixed, at=ago(1), tags=CC | {"work_type": "code"}),
               project="pegadocrag")
    data = await jobs(client, runtime="claude-code@hermes")
    assert list(by_ref(data)) == [mixed]
    m = by_ref(data)[mixed]
    assert (m["task_count"], m["llm_request_count"], m["prompt_tokens"]) == (2, 2, 20)  # both tasks, full totals
    assert (m["runtimes"], m["work_type"], m["work_types"]) == (["claude-code@hermes", "hermes-agent"], None,
                                                               ["code", "review"])
    assert list(by_ref(await jobs(client, work_type="code"))) == [mixed]


async def test_jobs_window_is_whole_job(client):
    await post(client, event("o1", session="o", turn="t1", job=JOB_A, at=ago(24 * 40)),
               event("o2", session="o", turn="t2", job=JOB_A, at=ago(24)),
               event("p1", session="p", turn="t1", job=JOB_B, at=ago(24 * 40)))
    recent = by_ref(await jobs(client, days=30))
    assert list(recent) == [JOB_A] and recent[JOB_A]["llm_request_count"] == 2
    assert set(by_ref(await jobs(client, days=90))) == {JOB_A, JOB_B}


async def test_conflicts_events_vs_tasks(client):
    at = ago(3)
    await post(client, *[event(f"t{n}", session="s", turn="T", job=JOB_A, at=at) for n in range(4)])
    await post(client, event("x-b", session="s", turn="T", job=JOB_B, at=at),
               event("x-c", session="s", turn="T", job=JOB_C, at=at))
    await post(client, event("x-b", session="s", turn="T", job=JOB_B, at=at))  # duplicate delivery
    data = await jobs(client)
    a = by_ref(data)[JOB_A]
    assert (a["conflict_count"], a["conflict_task_count"], a["llm_request_count"]) == (2, 1, 6)
    await post(client, event("u1", session="s", turn="U", job=JOB_A, at=at),
               event("u2", session="s", turn="U", job=JOB_B, at=at))
    data = await jobs(client)
    a = by_ref(data)[JOB_A]
    assert (a["conflict_count"], a["conflict_task_count"]) == (3, 2)
    assert (data["anomalies"]["job_ref_conflicts"], data["anomalies"]["job_ref_conflict_tasks"]) == (3, 2)


async def test_jobs_exclude_invalid_attribution(client):
    await post(client, event("ok", session="a", turn="t", job=JOB_A, at=ago(1)),
               event("bad", session="b", turn="t", job=JOB_A, at=ago(1), tags={"runtime": "zz-bogus"}))
    data = await jobs(client)
    a = by_ref(data)[JOB_A]
    assert (a["llm_request_count"], a["task_count"], a["prompt_tokens"]) == (1, 1, 10)
    assert data["anomalies"]["invalid_attribution_events"] == 1


async def test_jobs_legacy_tag_values_never_returned(client):
    """code-r1 p1-F2: pre-v11 tags were never normalized; /api/jobs re-applies the value rules on read."""
    await post(client, event("ok", session=None, turn=None, job=JOB_A, at=ago(1)),
               event("leg1", session=None, turn=None, at=ago(1)), event("leg2", session=None, turn=None, at=ago(1)))
    legacy = {"leg1": {"job_ref": "/home/zz-canary-user/private", "work_type": "zz-canary-host.internal"},
              "leg2": {"job_ref": JOB_A, "runtime": "zz-canary-runtime", "work_type": "zz-canary-work"}}
    async with AsyncSessionLocal() as session:  # written as old code would have stored them
        for cid, tags in legacy.items():
            await session.execute(text("UPDATE token_events SET tags_json = :t WHERE client_event_id = :c"),
                                  {"t": json.dumps(tags), "c": cid})
        await session.commit()
    response = await client.get("/api/jobs")
    assert response.status_code == 200 and "zz-canary" not in response.text
    data = response.json()
    assert [i["job_ref"] for i in data["items"]] == [JOB_A]
    assert (by_ref(data)[JOB_A]["work_types"], by_ref(data)[JOB_A]["runtimes"]) == (["other"], ["hermes-agent"])
