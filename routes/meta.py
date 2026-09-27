from fastapi import APIRouter

import features
from migrations import LATEST_SCHEMA_VERSION

router = APIRouter(tags=["meta"])


@router.get("/api/meta")
async def meta():
    state = features.current()
    return {
        "schema_version": LATEST_SCHEMA_VERSION,
        "task_prompt_capture": state.capture_enabled,
        "task_prompt_capture_disabled_reason": state.capture_disabled_reason,
        "jev_enabled": state.jev_enabled,
        "jev_disabled_reason": state.jev_disabled_reason,
        "config_errors": state.config_errors,
    }
