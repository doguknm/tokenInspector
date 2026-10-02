# Hermes code review r1 — part 7 of 10

TAG: `o9-job-correlation-cc-producer-code-r1-p7` · prompt: backend tests 2/4 — transcript, events, attribution, dedup, job correlation, jobs API.

FINDINGS:
F1 [MEDIUM] tests/test_jobs_api.py::test_jobs_exclude_evaluator — The evaluator fixture has neither session_id nor turn_id, so this test does not isolate evaluator exclusion from exclusion of events without a task — scenario: the jobs query loses its evaluator filter but still joins events to tasks → this evaluator remains excluded and the test passes — fix: also ingest an evaluator event with valid task identifiers and job attribution; verify its insertion and task linkage, then assert it contributes nothing to job counts, tokens or costs.
F2 [MEDIUM] tests/test_job_correlation.py::test_worst_case_plugin_tags_accepted — AC1.4 requires pinning the measured payload size; this test pins only the key count and acceptance, despite the Drift Log saying it pins 722 bytes. The separate cap-boundary checks do not pin the fixture measurement — scenario: fixture values grow beyond the recorded 768-byte decision threshold while remaining acceptable under the 1024-byte cap → all these assertions still pass — fix: assert the fixture’s canonical UTF-8 serialization is exactly 722 bytes and directly assert that the complete fixture passes _clean_tags before reserved-value normalization.
F3 [LOW] tests/test_cc_transcript.py::test_oversized_line_never_held_whole — The assertions check the returned classification, not bounded reading or allocation — scenario: _read_line regresses to reading the entire oversized line before returning ("oversized", None, size) → this test still passes despite violating its named control — fix: use an instrumented stream that rejects unbounded reads/readline calls and checks bounded read sizes; retain the existing classification assertions.

UNSURE:
- These are test-control findings, not observed production failures. Only the supplied content was reviewed; no tests were run, repository copies inspected or PEGA calls attempted.
- cc_support and production implementations are absent, so fixture construction, actual reader boundaries, transport behavior and backend transaction semantics cannot be verified.
- Full AC2.6 privacy coverage cannot be established here: the visible canary tests cover selected resolver/event values, not every emitted payload and persisted state. Broader sentinel tests may appear in other parts.
- Cross-producer double-counting protection beyond runtime/producer pairing, and replay ownership across main/forked subagent copies, cannot be established from these tests alone.
- The integration fixture raises HOOK_BOUND_S to 60 seconds and SEND_DEADLINE_S to 30 seconds; these tests therefore provide no evidence for the production five-second bound. Dedicated deadline/concurrency tests may be elsewhere.
- Export cursor/snapshot stability, v11/v12 migration and rollback safety, ingest_seq high-water persistence, repair transactions, installer ownership/atomic writes, and remaining AC coverage require the other review parts.
- Fixture parity checks skip without an adjacent plugin checkout; whether integration CI guarantees that these checks execute is not supplied.

VERDICT: Changes requested to the identified test controls; this part alone does not establish a production correctness defect or complete AC coverage.
