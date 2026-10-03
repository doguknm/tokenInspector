from sqlalchemy import text

from database import AsyncSessionLocal
from routes.events import _clean_agent


def test_clean_agent_accepts_supported_shapes():
    for value in ("main", "custom", "unknown", "fork", "plugin:agent", "a-0123456789abcdef"):
        assert _clean_agent(value) == value


def test_clean_agent_is_fail_open_and_never_echoes_invalid_value():
    secret = "https://secret.example/path"
    for value in (secret, "", None, 7, "x" * 65, "a-0123456789abcde."):
        assert _clean_agent(value) is None


async def test_agent_shapes_store_and_invalid_is_accepted(client):
    values = ["main", "custom", "unknown", "fork", "plugin:agent", "a-0123456789abcdef",
              "bad.value", "x" * 65, 7]
    for index, value in enumerate(values):
        response = await client.post("/api/events", headers={"X-Project-Name": "proj"}, json={
            "client_event_id": f"agent-{index}", "model": "m", "agent": value,
        })
        assert response.status_code == 201, response.text
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(text(
            "SELECT agent FROM token_events ORDER BY client_event_id"
        ))).scalars().all()
    assert rows == ["main", "custom", "unknown", "fork", "plugin:agent", "a-0123456789abcdef", None, None, None]
