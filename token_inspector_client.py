from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import httpx


def sha256_prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    error_message: Optional[str] = None
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
    prompt_text: Optional[str] = None
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
        self._closed = False
        self.dropped_events = 0

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
            return False
        payload = event.to_payload() if isinstance(event, TokenInspectorEvent) else dict(event)
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
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
            await self._client.post(
                f"{self.base_url}/api/events/batch",
                headers=self._headers(),
                json={"events": batch},
            )
        except Exception:
            pass

    async def _drain(self) -> None:
        while not self._closed or not self._queue.empty():
            batch: list[dict[str, Any]] = []
            deadline = time.monotonic() + self.flush_interval_seconds
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
            await self._send(batch)
            if not batch and self._closed:
                break

    async def close(self, timeout: float = 2.0) -> None:
        if self._closed:
            return
        self._closed = True
        if self._worker is not None:
            try:
                await asyncio.wait_for(self._worker, timeout=timeout)
            except Exception:
                self._worker.cancel()
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass

    async def __aenter__(self) -> "TokenInspectorClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()
