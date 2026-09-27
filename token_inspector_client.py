from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import httpx


# Never sent by this client: prompt text and free-form error text (O10 AC11).
SENSITIVE_FIELDS = ("prompt_text", "task_prompt_text", "error_message")
_ACK_KEYS = ("inserted", "duplicates", "rejected")


def sha256_prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def valid_ack(body: Any, n: int) -> bool:
    """A batch ack: non-bool, non-negative int inserted/duplicates/rejected summing to the batch size."""
    if not isinstance(body, dict):
        return False
    values = [body.get(key) for key in _ACK_KEYS]
    if not all(type(v) is int and v >= 0 for v in values):
        return False
    return sum(values) == n


@dataclass
class TokenInspectorEvent:
    model: str
    client_event_id: Optional[str] = None
    provider: Optional[str] = None
    model_requested: Optional[str] = None
    event_type: str = "llm_request"
    session_id: Optional[str] = None
    task_id: Optional[str] = None
    turn_id: Optional[str] = None
    turn_index: Optional[int] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    parent_span_id: Optional[str] = None
    api_request_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    role: Optional[str] = None
    environment: Optional[str] = None
    platform: Optional[str] = None
    user_id_hash: Optional[str] = None
    tags: dict[str, Any] = field(default_factory=dict)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: Optional[int] = None
    input_tokens_include_cache: bool = False
    status: str = "success"
    http_status: Optional[int] = None
    finish_reason: Optional[str] = None
    error_type: Optional[str] = None
    attempt: int = 1
    retry_count: int = 0
    ttft_ms: Optional[int] = None
    process_time_ms: Optional[int] = None
    request_size_bytes: Optional[int] = None
    response_size_bytes: Optional[int] = None
    tool_call_count: Optional[int] = None
    occurred_at: Optional[str] = None
    prompt_hash: Optional[str] = None
    prompt_length: Optional[int] = None
    complexity: Optional[int] = None

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        return {key: value for key, value in payload.items() if value is not None}


class TokenInspectorClient:
    def __init__(
        self,
        *,
        project_name: str,
        base_url: str = "http://127.0.0.1:8100",
        ingest_token: Optional[str] = None,
        queue_max: int = 1000,
        batch_max: int = 50,
        flush_interval_seconds: float = 1.0,
        timeout_seconds: float = 1.5,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.project_name = project_name
        self.base_url = base_url.rstrip("/")
        self.ingest_token = ingest_token
        self.batch_max = max(1, batch_max)
        self.flush_interval_seconds = max(0.05, flush_interval_seconds)
        self.timeout_seconds = max(0.05, timeout_seconds)
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=max(1, queue_max))
        self._worker: Optional[asyncio.Task] = None
        self._client: Optional[httpx.AsyncClient] = None
        self._transport = transport
        self._closed = False
        # Every event passed to post_event ends in exactly one counter.
        self.delivered_events = 0
        # confirmed loss
        self.dropped_events = 0  # queue full
        self.serialization_failed = 0
        self.rejected_events = 0
        self.http_failures = 0
        self.dropped_on_close = 0
        # unconfirmed delivery: the server may have committed these
        self.transport_unconfirmed = 0
        self.malformed_acks = 0

    @property
    def lost_events(self) -> int:
        return (self.dropped_events + self.serialization_failed + self.rejected_events
                + self.http_failures + self.dropped_on_close)

    @property
    def unconfirmed_events(self) -> int:
        return self.transport_unconfirmed + self.malformed_acks

    def _headers(self) -> dict[str, str]:
        headers = {"X-Project-Name": self.project_name}
        if self.ingest_token:
            headers["X-Ingest-Token"] = self.ingest_token
        return headers

    def _ensure_worker(self) -> None:
        if self._closed or (self._worker is not None and not self._worker.done()):
            return
        try:
            self._worker = asyncio.create_task(self._drain())
        except Exception:
            self._worker = None

    async def post_event(self, event: TokenInspectorEvent | dict[str, Any]) -> bool:
        if self._closed:
            self.dropped_on_close += 1
            return False
        try:
            # A stable id, written back to the caller's object, so a resend reuses it.
            if isinstance(event, TokenInspectorEvent):
                if not event.client_event_id:
                    event.client_event_id = uuid.uuid4().hex
                payload = event.to_payload()
            else:
                if not (event.get("client_event_id") or event.get("event_id")):
                    event["client_event_id"] = uuid.uuid4().hex
                payload = dict(event)
            for key in SENSITIVE_FIELDS:
                payload.pop(key, None)
            json.dumps(payload)  # a payload that cannot be serialized never poisons a batch
        except Exception:
            self.serialization_failed += 1
            return False
        try:
            self._queue.put_nowait(payload)
        except (asyncio.QueueFull, Exception):
            self.dropped_events += 1
            return False
        self._ensure_worker()
        return True

    async def post_batch(self, events: list[TokenInspectorEvent | dict[str, Any]]) -> int:
        accepted = 0
        for event in events:
            accepted += int(await self.post_event(event))
        return accepted

    async def _send(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        n = len(batch)
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.timeout_seconds, transport=self._transport)
            response = await self._client.post(
                f"{self.base_url}/api/events/batch",
                headers=self._headers(),
                json={"events": batch},
            )
        except asyncio.CancelledError:
            self.transport_unconfirmed += n
            raise
        except Exception:
            self.transport_unconfirmed += n
            return
        if not 200 <= response.status_code < 300:
            self.http_failures += n
            return
        try:
            body = response.json()
        except Exception:
            body = None
        if not valid_ack(body, n):
            self.malformed_acks += n
            return
        self.delivered_events += body["inserted"] + body["duplicates"]
        self.rejected_events += body["rejected"]

    async def _drain(self) -> None:
        while not self._closed or not self._queue.empty():
            batch: list[dict[str, Any]] = []
            deadline = time.monotonic() + self.flush_interval_seconds
            try:
                while len(batch) < self.batch_max:
                    timeout = max(0.0, deadline - time.monotonic())
                    try:
                        item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                        batch.append(item)
                        self._queue.task_done()
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        break
            except asyncio.CancelledError:
                self.dropped_on_close += len(batch)  # collected but never sent
                raise
            await self._send(batch)
            if not batch and self._closed:
                break

    async def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=timeout)  # drains, then cancels on timeout
            except (Exception, asyncio.CancelledError):
                self._worker.cancel()
        while not self._queue.empty():  # still queued after the drain window
            self._queue.get_nowait()
            self.dropped_on_close += 1
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass

    async def __aenter__(self) -> "TokenInspectorClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
