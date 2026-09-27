# Plan review r2 — triage (2026-09-27) — FINAL ROUND (no round 3)

Sources: `task-telemetry-jev-pilot-plan-r2-A.md` (7 findings), `task-telemetry-jev-pilot-plan-r2-B.md` (5 findings + 2 UNSURE contract gaps).
User decision: **all 13 items FIXED.** Round-2 fixes are verified by tests (no re-review).

| # | Finding | Approved fix (each needs a test) |
|---|---|---|
| 1 | A-F1 WAL checkpoint retry depends on new row changes | Commit deletion first; persist a pending-checkpoint flag; retry TRUNCATE independently of affected rows; test busy → no new expiries → truncation succeeds |
| 2 | A-F2 capture off allows purge off while data remains | Retention/purge stays scheduled while any retained prompt/note/backup exists; interval 0 refused unless a verified all-copy purge completed; no unconditional max-lag claim — failures surfaced |
| 3 | A-F3 producer spool/dead-letter/.tmp expiry by file age | Capture-time based expiry across spool, dead-letter and orphan `.json.tmp` at startup and maintenance; keep non-content telemetry when possible; tests: delayed spooling, dead-letter move, interrupted write |
| 4 | A-F4 Retry-After only per task | Persist per-provider `not_before` deadlines across tasks, runs and restarts; enforce on every call; test both bypasses |
| 5 | A-F5 completion depends on arrival order | Recompute completion from event timestamps (project+session scope) after late arrivals, incl. session-end marker arriving first; reversed-arrival tests |
| 6 | A-F6 start-complexity candidate rule differs backfill vs ingest | One approved-provenance candidate rule applied before (time, event id) ordering in both paths; historical inference exception explicit; mixed-method tests |
| 7 | A-F7 labelling progress / labeler not persisted | Durable metadata-only labelling-progress record (labelled / skipped_by_labeler / not_labelled, labeler id); `score --labeler`; tests: resume, skip, multiple labelers |
| 8 | B-F1 activation gate unreachable | Split: (a) approval + config preflight with flags off (`not_enabled` accepted), (b) controlled activation, (c) post-start verification requires no disabled reason |
| 9 | B-F2 expired fixture purged at startup | Test `expired` with injected clock or stubbed response; keep a real startup-purge test expecting `purged` |
| 10 | B-F3 out-of-range page test is not out of range | total=120, page_size=50, request page 4 → empty → exactly one reload of page 3 with remaining rows + correct button states |
| 11 | B-F4 detail Close does not invalidate in-flight response | Close increments `taskDetailGen`; closed state authoritative; test delayed success/error after close |
| 12 | B-F5 chart-only failure untested (LOW) | Playwright stub: chart 500 while table succeeds → chart error shown, defined stale-chart handling, table unaffected |
| 13 | B UNSURE `children` shape and `projects` filter semantics | Define `children[]` element fields (incl. `jev_raw_score`) and state whether `projects` ignores the project filter (it must, so the selector keeps all options) |
