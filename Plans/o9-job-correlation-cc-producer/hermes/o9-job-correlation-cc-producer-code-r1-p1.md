# Hermes code review r1 — part 1 of 10

TAG: `o9-job-correlation-cc-producer-code-r1-p1` · prompt: backend code 1/5 — routes, models, migrations, task store.

FINDINGS:
F1 [HIGH] routes/export.py::_model / _event_item — The model value rule permits paths and hostnames, violating AC3.2’s value-level privacy requirement — scenario: a stored model or pricing_model of `home/alice/private.txt` or `private-host.internal` matches MODEL_RE, contains no `//`, and is exported unchanged — fix: restrict model values to approved model identifiers/namespaces; suppress or pseudonymize other values, with path and hostname canaries inside both allowed fields.
F2 [HIGH] routes/jobs.py::_EVENTS_CTE / _item — The new Jobs endpoint trusts historical tag values without applying reserved-key value rules — scenario: a pre-v11 event without a task has `job_ref="/home/alice/private"` and `work_type="private-host.internal"` in tags; the query groups it as a job and the response returns both strings unchanged, including the work-type filter option — fix: validate job references and normalize runtime/work-type/attempt values when reading legacy rows, before grouping and producing filter options; add legacy-row privacy tests.
F3 [MEDIUM] routes/export.py::_export / _decode_cursor — Malformed numeric inputs can escape the documented static validation errors — scenario: an ASCII digit-only limit exceeding Python’s integer-string conversion limit raises ValueError at `int(limit_raw)`; alternatively, a structurally valid cursor containing an integer `s` outside SQLite’s signed-integer range passes decoding and fails during SQL binding, outside the OperationalError handler — fix: bound numeric string lengths before conversion, catch conversion failures, and validate cursor sequence/revision/key ranges before SQL; return static invalid_limit/invalid_cursor responses.
UNSURE:
- No test files were supplied. AC coverage, value-level sentinel coverage, migration fixtures, mutation checks and passing suite results cannot be verified.
- Export tasks read live hierarchy, completion and updated_at fields, while aggregates use snapshot events. Whether those metadata fields must also remain snapshot-stable needs the export contract and _completion implementation.
- Initial export pages do not recheck revision/epoch after their reads. Transaction configuration is omitted, so consistency during concurrent recost/repair and the accepted later-page invalidation guarantee cannot be fully established.
- Full migration runner, SQLite transaction configuration, backup implementation and rollback_schema.py are absent; migration refusal atomicity, rollback ordering and epoch-reset safety remain unverified.
- The ingest caller is omitted; whether duplicate events bypass task upserts, conflict increments and other side effects—and whether acknowledgements correctly report cross-project duplicates—cannot be established here.
- Repair transaction safety, producer finality/dedup, hook latency/concurrency, installer ownership/atomicity, plugin privacy and frontend behavior belong to the remaining parts.
- Review was limited to the supplied content; no repository inspection, test execution, file changes or PEGA access was performed.
VERDICT: Changes requested for the privacy and input-validation findings; overall AC compliance remains unverified pending the remaining parts.
