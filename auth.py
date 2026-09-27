import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException

from features import origin_list


async def require_ingest_auth(
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
) -> None:
    expected = os.environ.get("INGEST_TOKEN")
    if not expected:
        return
    if x_ingest_token is None or not hmac.compare_digest(x_ingest_token, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest token")


async def require_sensitive_auth(
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
    origin: Optional[str] = Header(default=None),
) -> None:
    """Prompt reads, labels, evaluate and purge: a token is always required, even with flags off."""
    expected = os.environ.get("INGEST_TOKEN")
    if not expected:
        raise HTTPException(status_code=403, detail="auth_not_configured")
    if origin is not None and origin.rstrip("/") not in origin_list():
        raise HTTPException(status_code=403, detail="origin_not_allowed")
    if x_ingest_token is None or not hmac.compare_digest(x_ingest_token, expected):
        raise HTTPException(status_code=401, detail="invalid_token")
