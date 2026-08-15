import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException


async def require_ingest_auth(
    x_ingest_token: Optional[str] = Header(default=None, alias="X-Ingest-Token"),
) -> None:
    expected = os.environ.get("INGEST_TOKEN")
    if not expected:
        return
    if x_ingest_token is None or not hmac.compare_digest(x_ingest_token, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest token")
