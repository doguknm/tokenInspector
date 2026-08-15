import asyncio

from token_inspector_client import TokenInspectorClient, TokenInspectorEvent, sha256_prompt_hash


async def test_client_is_fail_open_when_server_is_down():
    client = TokenInspectorClient(
        project_name="hermes",
        base_url="http://127.0.0.1:1",
        timeout_seconds=0.05,
        flush_interval_seconds=0.05,
    )
    accepted = await client.post_event(
        TokenInspectorEvent(model="m", client_event_id="e", prompt_tokens=1)
    )
    assert accepted is True
    await asyncio.sleep(0.15)
    await client.close()


def test_prompt_hash_is_deterministic():
    assert sha256_prompt_hash("hello") == sha256_prompt_hash("hello")
    assert len(sha256_prompt_hash("hello")) == 64


async def test_queue_full_drops_without_blocking():
    client = TokenInspectorClient(
        project_name="hermes",
        base_url="http://127.0.0.1:1",
        queue_max=1,
        flush_interval_seconds=10,
    )
    client._ensure_worker = lambda: None
    assert await client.post_event({"model": "m", "client_event_id": "1"}) is True
    assert await client.post_event({"model": "m", "client_event_id": "2"}) is False
    assert client.dropped_events == 1
    await client.close()
