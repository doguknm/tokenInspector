import asyncio

from sqlalchemy import func, select

from database import AsyncSessionLocal
from models import TokenEvent


async def _post(client, project="hermes", event_id="same"):
    return await client.post(
        "/api/events",
        headers={"X-Project-Name": project},
        json={
            "model": "claude-sonnet-4-6",
            "client_event_id": event_id,
            "prompt_tokens": 10,
            "completion_tokens": 5,
        },
    )


async def test_concurrent_idempotency_is_atomic(client):
    responses = await asyncio.gather(*[_post(client) for _ in range(20)])
    assert all(response.status_code in {200, 201} for response in responses)
    assert sum(response.status_code == 201 for response in responses) == 1
    ids = {response.json()["id"] for response in responses}
    assert len(ids) == 1

    async with AsyncSessionLocal() as session:
        count = (
            await session.execute(
                select(func.count(TokenEvent.id)).where(
                    TokenEvent.project_name == "hermes",
                    TokenEvent.client_event_id == "same",
                )
            )
        ).scalar_one()
    assert count == 1


async def test_idempotency_is_scoped_by_project(client):
    first, second = await asyncio.gather(
        _post(client, project="hermes", event_id="shared"),
        _post(client, project="another", event_id="shared"),
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


async def test_batch_reports_inserted_duplicates_and_rejected(client):
    response = await client.post(
        "/api/events/batch",
        headers={"X-Project-Name": "hermes"},
        json={
            "events": [
                {"model": "claude-sonnet-4-6", "client_event_id": "batch-1", "prompt_tokens": 1},
                {"model": "claude-sonnet-4-6", "client_event_id": "batch-1", "prompt_tokens": 1},
                {"model": "claude-sonnet-4-6", "client_event_id": "bad", "prompt_tokens": -1},
            ]
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["accepted"] == 3
    assert body["inserted"] == 1
    assert body["duplicates"] == 1
    assert body["rejected"] == 1
    assert body["inserted"] + body["duplicates"] + body["rejected"] == body["accepted"]


async def test_batch_limit(client):
    response = await client.post(
        "/api/events/batch",
        headers={"X-Project-Name": "hermes"},
        json={"events": [{"model": "m"} for _ in range(501)]},
    )
    assert response.status_code == 422
