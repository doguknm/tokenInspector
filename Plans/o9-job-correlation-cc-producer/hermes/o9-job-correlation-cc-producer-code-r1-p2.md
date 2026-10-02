# Hermes code review r1 — part 2 of 10

TAG: `o9-job-correlation-cc-producer-code-r1-p2` · prompt: backend code 2/5 — dashboard, scripts, CI, export contract doc.

FINDINGS:
F1 [HIGH] docs/export-contract-v1.md — Documented value rules permit excluded paths and free error text, beyond the stated hostname residual — scenario: model="home/alice/private.txt" passes the model regex; error_type="SecretCanary" passes the purported “closed class” rule and leaves unchanged. Neither rule enforces the promised exclusion of content inside allowed fields — fix: use a constrained model namespace and an actual error-class allowlist, with null/other fallbacks; add value-level canaries for these shapes and align implementation with the corrected contract.
F2 [MEDIUM] scripts/repair_task_parents.py:backup — Backup filenames have only second-level uniqueness and existing destinations are silently reused — scenario: two apply runs start within the same UTC second; the first repairs the database, then the second copies the repaired database onto the same backup filename, destroying the pre-repair recovery snapshot. Both verifications can pass — fix: reserve a unique backup destination exclusively and never overwrite an existing backup; test repeated and concurrent invocations with a fixed timestamp.
F3 [MEDIUM] static/app.js:fetchAllExport — The dashboard does not implement the contract’s 30-second client timeout — scenario: an export request connects but never finishes its response body; fetch/response.json remains pending indefinitely, with no fixed failure message unless the user starts another export — fix: enforce a per-page deadline covering both fetch and body consumption, clear its timer reliably, and distinguish timeout from supersession; test a stalled response.
F4 [LOW] docs/export-contract-v1.md:Fields:events, role — The published role value set disagrees with the accepted Phase 3 Drift Log — scenario: a counted non-JEV event has role="evaluator"; the Drift Log explicitly allows that value, while the contract says only primary/subagent survive and every other value becomes other — fix: reconcile the contract with the accepted rule and pin the evaluator case in the contract tests.

UNSURE:
- Review is static and limited to the supplied content; no repository inspection, test execution, file changes or PEGA access was performed.
- Export serializers and privacy tests are absent from this part: F1 establishes an unsafe documented rule, not an observed exported payload.
- No test bodies are supplied, so coverage and passing results for repair, rollback, Jobs rendering, CSV download and the other acceptance criteria cannot be confirmed.
- AC-DB1.3 requires v12→v10 to refuse without a separate v12→v11 invocation, while AC-DB3.5 explicitly requires the chained rollback implemented here. These lane criteria need reconciliation; the implementation cannot satisfy both.
- Migration/re-upgrade code is not supplied: persistent ingest_seq high-water, epoch rotation, cursor invalidation after rollback and old-code recovery cannot be verified.
- Backend query code is not supplied: snapshot stability, dedup, whole-job filter semantics and export revision checks cannot be verified. Repair does visibly update revision within its write transaction when changes occur.
- Producer, hook and installer code is outside this part; fail-open timing, concurrent-hook safety, payload/state privacy, installer ownership and atomic replacement remain unverified.

VERDICT: Changes requested for the documented privacy rules, backup preservation and dashboard timeout; full acceptance and test coverage remain unverified.
