"""Claude Code request input-size complexity. Stdlib only."""

from __future__ import annotations

from typing import Optional

METHOD = "cc-input-size-v1"


def input_size_tier(input_tokens, cache_read, cache_creation) -> Optional[int]:
    values = (input_tokens, cache_read, cache_creation)
    if any(type(value) is not int or value < 0 for value in values):
        return None
    total = sum(values)
    if total < 4000:
        return 1
    if total < 16000:
        return 2
    if total < 64000:
        return 3
    if total < 128000:
        return 4
    return 5
