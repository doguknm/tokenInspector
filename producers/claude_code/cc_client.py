"""Batch POST to Token Inspector with ack validation (O9 C6). Stdlib only (urllib).

Returns the validated ack or None on any failure (401, 403, 422, 5xx, timeout, connection error,
malformed ack). Never logs; the token is only ever put in the request header.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Optional

TIMEOUT_S = 2.0
BATCH_SIZE = 200
_ACK_KEYS = ("inserted", "duplicates", "rejected")


def valid_ack(body: Any, n: int) -> bool:
    """Copy of token_inspector_client.valid_ack (stdlib-only producer; parity test in the suite)."""
    if not isinstance(body, dict):
        return False
    values = [body.get(key) for key in _ACK_KEYS]
    if not all(type(v) is int and v >= 0 for v in values):
        return False
    return sum(values) == n


def post_batch(url: str, token: Optional[str], project: str, events: list[dict[str, Any]]) -> Optional[dict]:
    headers = {"Content-Type": "application/json", "X-Project-Name": project}
    if token:
        headers["X-Ingest-Token"] = token
    data = json.dumps({"events": events}, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(url + "/api/events/batch", data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            if not 200 <= response.status < 300:
                return None
            body = json.loads(response.read(1024 * 1024).decode("utf-8"))
    except Exception:
        return None
    return body if valid_ack(body, len(events)) else None
