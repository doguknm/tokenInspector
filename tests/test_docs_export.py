"""O9 X6 / AC3.8: the export contract document covers every field, error and limit the router implements."""

from pathlib import Path

from routes.export import EXPORT_FIELDS, EXPORT_TAG_KEYS, MAX_LIMIT, MAX_SPAN, SCHEMA_VERSION

DOCS = Path(__file__).resolve().parents[1] / "docs"


def test_export_contract_documented():
    text = (DOCS / "export-contract-v1.md").read_text(encoding="utf-8")
    for dataset, fields in EXPORT_FIELDS.items():
        assert f"/api/export/v1/{dataset}" in text
        for field in fields:
            assert f"`{field}`" in text, (dataset, field)
    for key in EXPORT_TAG_KEYS:
        assert f"`{key}`" in text
    for code in ("invalid_range", "invalid_limit", "invalid_cursor", "snapshot_expired", "busy"):
        assert f'"error": "{code}"' in text, code
    for phrase in (f"{MAX_SPAN.days} days", str(MAX_LIMIT), f"`schema_version: {SCHEMA_VERSION}`",
                   "consumers must ignore unknown fields", "**inclusive**", "**exclusive**",
                   "start-time cohort", "full replacement", "Cursor lifetime rule", "valid until `snapshot_expired`",
                   "Residual (value privacy)", "Retry-After: 5", "client timeout of 30 s",
                   "JSON is for machines; CSV is for people", "`primary`, `subagent`, `evaluator`",
                   "`evaluator` appears only for non-excluded events"):
        assert phrase in text, phrase
    assert (DOCS / "adr" / "005-export-contract.md").is_file()
