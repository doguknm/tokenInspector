"""O10 safe producer example: token_inspector_client.py and the README push_token_event helper (AC11, AC12)."""

import asyncio
import dataclasses
import inspect
import json
import re
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

import token_inspector_client as tic
from database import AsyncSessionLocal
from main import app
from token_inspector_client import TokenInspectorClient, TokenInspectorEvent, valid_ack

README = Path(__file__).resolve().parent.parent / "README.md"
HEX32 = re.compile(r"^[0-9a-f]{32}$")


def _ack(inserted=0, duplicates=0, rejected=0):
    return {"inserted": inserted, "duplicates": duplicates, "rejected": rejected, "items": []}


def _ok_handler(record=None):
    def handler(request):
        events = json.loads(request.content)["events"]
        if record is not None:
            record.append(request)
        return httpx.Response(200, json=_ack(inserted=len(events)))
    return handler


def _client(handler, **kwargs):
    kwargs.setdefault("flush_interval_seconds", 0.05)
    return TokenInspectorClient(project_name="demo", transport=httpx.MockTransport(handler), **kwargs)


def _body(request):
    return json.loads(request.content)


def _accounted(client):
    return client.delivered_events + client.lost_events + client.unconfirmed_events


# --- AC11 -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["secret-ingest-token", None])
async def test_sends_token_and_posts_only_to_batch(token):
    seen = []
    client = _client(_ok_handler(seen), ingest_token=token)
    for i in range(3):
        assert await client.post_event({"model": "m", "prompt_tokens": i}) is True
    await client.close()
    assert seen and all(r.url.path == "/api/events/batch" and r.method == "POST" for r in seen)
    for request in seen:
        assert request.headers["X-Project-Name"] == "demo"
        assert request.headers.get("X-Ingest-Token") == token
    assert client.delivered_events == 3


async def _scenario(handler, events, **kwargs):
    client = _client(handler, **kwargs)
    for event in events:
        await client.post_event(event)
    await client.close(timeout=2.0)
    assert _accounted(client) == len(events)  # every event ends in exactly one counter
    return client


async def test_loss_counters_sum_to_every_lost_event():
    ev = lambda: {"model": "m"}  # noqa: E731

    # queue full (worker held back), then the queued ones are dropped on close
    full = TokenInspectorClient(project_name="demo", queue_max=2, transport=httpx.MockTransport(_ok_handler()))
    full._ensure_worker = lambda: None
    results = [await full.post_event(ev()) for _ in range(3)]
    await full.close()
    assert results == [True, True, False]
    assert (full.dropped_events, full.dropped_on_close, _accounted(full)) == (1, 2, 3)

    bad = await _scenario(_ok_handler(), [{"model": "m", "tags": {"x": object()}}, ev()])
    assert (bad.serialization_failed, bad.delivered_events) == (1, 1)

    failed = await _scenario(lambda r: httpx.Response(500, json={"detail": "boom"}), [ev(), ev()])
    assert (failed.http_failures, failed.lost_events, failed.unconfirmed_events) == (2, 2, 0)

    rejected = await _scenario(lambda r: httpx.Response(200, json=_ack(rejected=2)), [ev(), ev()])
    assert (rejected.rejected_events, rejected.lost_events) == (2, 2)

    def refuse(request):
        raise httpx.ConnectError("down")

    transport = await _scenario(refuse, [ev(), ev(), ev()])
    assert (transport.transport_unconfirmed, transport.unconfirmed_events, transport.lost_events) == (3, 3, 0)

    garbage = await _scenario(lambda r: httpx.Response(200, text="not json"), [ev()])
    assert (garbage.malformed_acks, garbage.unconfirmed_events, garbage.lost_events) == (1, 1, 0)

    closed = _client(_ok_handler())
    await closed.close()
    assert await closed.post_event(ev()) is False
    assert (closed.dropped_on_close, _accounted(closed)) == (1, 1)

    mixed = await _scenario(lambda r: httpx.Response(200, json=_ack(inserted=1, duplicates=1, rejected=1)),
                            [ev(), ev(), ev()])
    assert (mixed.delivered_events, mixed.rejected_events) == (2, 1)


async def test_wire_invalid_events_never_poison_a_batch():
    """code-r1 P1 F3: validation matches httpx's JSON body (allow_nan=False, UTF-8)."""
    seen = []
    client = _client(_ok_handler(seen))
    lone_surrogate = chr(0xD800)
    events = [{"model": "ok1"}, {"model": "m", "prompt_tokens": float("nan")}, {"model": "m" + lone_surrogate},
              {"model": "m", "tags": {"x": float("inf")}}, {"model": "ok2"}]
    results = [await client.post_event(e) for e in events]
    await client.close()
    assert results == [True, False, False, False, True]
    assert (client.serialization_failed, client.delivered_events, client.transport_unconfirmed) == (3, 2, 0)
    assert [e["model"] for r in seen for e in _body(r)["events"]] == ["ok1", "ok2"]


async def test_close_cancels_and_counts():
    started, release = asyncio.Event(), asyncio.Event()

    async def blocking(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=_ack(inserted=1))

    client = _client(blocking, batch_max=1, flush_interval_seconds=0.01)
    await client.post_event({"model": "m"})
    await asyncio.wait_for(started.wait(), 2)
    for _ in range(3):
        assert await client.post_event({"model": "m"}) is True
    await client.close(timeout=0.1)
    assert (client.transport_unconfirmed, client.dropped_on_close, client.delivered_events) == (1, 3, 0)
    assert _accounted(client) == 4
    assert client._worker.done()


@pytest.mark.parametrize(
    ("ack", "valid"),
    [
        ((0, 0, 0), False), ((-1, 1, 1), False), ((True, 0, 0), False), ((1.0, 0, 0), False),
        (("1", 0, 0), False), (None, False), ((2, 0, 0), False),
        ((1, 0, 0), True), ((0, 1, 0), True), ((0, 0, 1), True),
    ],
)
async def test_ack_validator_rejects_impossible_acks(ack, valid):
    body = {"inserted": 1, "duplicates": 0} if ack is None else dict(zip(("inserted", "duplicates", "rejected"), ack))
    assert valid_ack(body, 1) is valid
    client = await _scenario(lambda r: httpx.Response(200, json=body), [{"model": "m"}])
    assert client.malformed_acks == (0 if valid else 1)


async def test_client_event_id_is_stable_uuid4():
    seen = []
    client = _client(_ok_handler(seen), batch_max=1)
    event = TokenInspectorEvent(model="m")
    await client.post_event(event)
    await client.post_event(event)
    raw = {"model": "m"}
    await client.post_event(raw)
    given = {"model": "m", "client_event_id": "caller-id"}
    await client.post_event(given)
    await client.close()
    ids = [e["client_event_id"] for r in seen for e in _body(r)["events"]]
    assert HEX32.fullmatch(event.client_event_id) and uuid.UUID(event.client_event_id).version == 4
    assert ids[0] == ids[1] == event.client_event_id
    assert HEX32.fullmatch(raw["client_event_id"]) and ids[2] == raw["client_event_id"]  # written back (F9)
    assert given["client_event_id"] == "caller-id" and ids[3] == "caller-id"


class RecordingASGI(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.ASGITransport(app=app)
        self.acks = []

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        self.acks.append(json.loads(body))
        return httpx.Response(response.status_code, headers=response.headers, content=body)


async def test_idless_dict_resent_inserts_once(client):
    transport = RecordingASGI()
    producer = TokenInspectorClient(project_name="demo", base_url="http://test", transport=transport,
                                    flush_interval_seconds=0.02)
    event = {"model": "m", "prompt_tokens": 1}
    await producer.post_event(event)
    for _ in range(200):
        if producer.delivered_events:
            break
        await asyncio.sleep(0.01)
    await producer.post_event(event)  # the same caller dict, resent
    await producer.close()
    async with AsyncSessionLocal() as session:
        count = (await session.execute(text("SELECT COUNT(*) FROM token_events"))).scalar()
    assert count == 1
    assert [(a["inserted"], a["duplicates"]) for a in transport.acks] == [(1, 0), (0, 1)]
    assert producer.delivered_events == 2 and producer.lost_events == 0


async def test_queue_stays_bounded():
    client = TokenInspectorClient(project_name="demo", queue_max=2, transport=httpx.MockTransport(_ok_handler()))
    client._ensure_worker = lambda: None
    results = [await asyncio.wait_for(client.post_event({"model": "m"}), 1) for _ in range(3)]
    assert results == [True, True, False] and client.dropped_events == 1
    await client.close()


async def test_sensitive_fields_refused_or_stripped():
    for field in ("prompt_text", "task_prompt_text", "error_message"):
        with pytest.raises(TypeError):
            TokenInspectorEvent(model="m", **{field: "x"})

    @dataclasses.dataclass
    class WithPrompt(TokenInspectorEvent):  # a subclass that adds the fields back is still stripped
        task_prompt_text: str | None = None
        error_message: str | None = None

    subclass_seen = []
    subclass_client = _client(_ok_handler(subclass_seen))
    await subclass_client.post_event(WithPrompt(model="m", task_prompt_text="tp", error_message="em"))
    await subclass_client.close()
    (sent_sub,) = _body(subclass_seen[0])["events"]
    assert "task_prompt_text" not in sent_sub and "error_message" not in sent_sub
    seen = []
    client = _client(_ok_handler(seen))
    await client.post_event({"model": "m", "prompt_text": "p", "task_prompt_text": "tp", "error_message": "em",
                             "error_type": "APIError", "http_status": 429, "status": "error"})
    await client.close()
    (sent,) = _body(seen[0])["events"]
    assert not {"prompt_text", "task_prompt_text", "error_message"} & set(sent)
    assert (sent["error_type"], sent["http_status"]) == ("APIError", 429)
    # Q5: prompt hash/length stay available
    assert len(tic.sha256_prompt_hash("x")) == 64
    kept = TokenInspectorEvent(model="m", prompt_hash=tic.sha256_prompt_hash("x"), prompt_length=1).to_payload()
    assert kept["prompt_length"] == 1 and "prompt_hash" in kept


# --- AC12 README helper -----------------------------------------------------------------------------------------


def _readme_helper() -> dict:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    (source,) = [b for b in blocks if "async def push_token_event" in b]
    namespace: dict = {"__name__": "readme_helper"}
    exec(compile(source, "README.md:push_token_event", "exec"), namespace)
    return namespace


async def test_readme_helper_contract(monkeypatch):
    monkeypatch.setenv("TOKEN_INSPECTOR_API_KEY", "readme-token-123")
    monkeypatch.setenv("TOKEN_INSPECTOR_PROJECT", "demo")
    ns = _readme_helper()
    params = inspect.signature(ns["push_token_event"]).parameters
    assert "prompt_text" not in params and "error_message" not in params
    assert "error_type" in params and "http_status" in params
    assert ns["valid_ack"] is tic.valid_ack
    client = ns["_token_inspector"]()
    assert client is ns["_token_inspector"]() and isinstance(client, TokenInspectorClient)

    started, release, seen = asyncio.Event(), asyncio.Event(), []

    async def blocking(request):
        seen.append(request)
        started.set()
        await release.wait()
        return httpx.Response(200, json=_ack(inserted=len(_body(request)["events"])))

    client._transport = httpx.MockTransport(blocking)
    client.flush_interval_seconds = 0.01
    assert await asyncio.wait_for(ns["push_token_event"]("m", prompt_tokens=1), 1) is True
    await asyncio.wait_for(started.wait(), 2)
    assert await asyncio.wait_for(ns["push_token_event"]("m", prompt_tokens=2), 1) is True  # handler still blocked
    release.set()
    await ns["close_token_inspector"]()
    assert seen[0].url.path == "/api/events/batch"
    assert seen[0].headers["X-Ingest-Token"] == "readme-token-123"
    sent = [e for r in seen for e in _body(r)["events"]]
    assert all(HEX32.fullmatch(e["client_event_id"]) for e in sent)
    assert client.delivered_events == 2

    ns["_client"] = None  # a fresh module-level client against a failing server
    failing = ns["_token_inspector"]()
    failing._transport = httpx.MockTransport(lambda r: httpx.Response(500, json={}))
    failing.flush_interval_seconds = 0.01
    assert await ns["push_token_event"]("m") is True
    await ns["close_token_inspector"]()
    assert failing.http_failures == 1


def _readme_batch() -> dict:
    blocks = re.findall(r"```python\n(.*?)```", README.read_text(encoding="utf-8"), re.S)
    (source,) = [b for b in blocks if "async def post_batch" in b]
    namespace: dict = {"__name__": "readme_batch"}
    exec(compile(source, "README.md:post_batch", "exec"), namespace)
    return namespace


@pytest.mark.parametrize(("handler", "expected"), [
    (lambda r: httpx.Response(200, json=_ack(inserted=2, duplicates=1)), {"delivered": 3, "lost": 0, "unconfirmed": 0}),
    (lambda r: httpx.Response(200, json=_ack(rejected=3)), {"delivered": 0, "lost": 3, "unconfirmed": 0}),
    (lambda r: httpx.Response(200, json=_ack(inserted=1, rejected=2)), {"delivered": 1, "lost": 2, "unconfirmed": 0}),
    (lambda r: httpx.Response(200, text="not json"), {"delivered": 0, "lost": 0, "unconfirmed": 3}),
    (lambda r: httpx.Response(200, json=_ack(inserted=5)), {"delivered": 0, "lost": 0, "unconfirmed": 3}),
    (lambda r: httpx.Response(500, json={}), {"delivered": 0, "lost": 3, "unconfirmed": 0}),
    ("refuse", {"delivered": 0, "lost": 0, "unconfirmed": 3}),
])
async def test_readme_batch_example_accounts_for_every_event(monkeypatch, handler, expected):
    """code-r1 P1 F4: a rejected event is lost, a malformed ack is unconfirmed, non-2xx is lost."""
    monkeypatch.setenv("TOKEN_INSPECTOR_API_KEY", "readme-token-123")
    ns = _readme_batch()
    assert ns["valid_ack"] is tic.valid_ack
    seen = []

    def record(request):
        seen.append(request)
        if handler == "refuse":
            raise httpx.ConnectError("down")
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as http:
        result = await ns["post_batch"](http, [{"model": "m", "client_event_id": f"e{i}"} for i in range(3)])
    assert result == expected
    assert seen[0].url.path == "/api/events/batch" and seen[0].headers["X-Ingest-Token"] == "readme-token-123"
