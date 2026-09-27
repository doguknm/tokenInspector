"""task-redact-v1 shared vectors (the plugin repo runs a byte-identical copy)."""

import json
from pathlib import Path

import pytest

from redaction import REDACTION_VERSION, scrub_text

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "redaction_vectors.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(vector):
    kwargs = {
        "extra_terms": vector.get("extra_terms", ()),
        "extra_host_suffixes": vector.get("extra_host_suffixes", ()),
    }
    scrubbed = scrub_text(vector["input"], **kwargs)
    assert scrubbed == vector["expected"]
    assert scrub_text(scrubbed, **kwargs) == scrubbed  # idempotent


def test_version():
    assert REDACTION_VERSION == "task-redact-v1"
