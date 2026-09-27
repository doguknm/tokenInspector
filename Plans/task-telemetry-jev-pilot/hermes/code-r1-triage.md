# Code review r1 — triage (2026-09-27)

Sources: `task-telemetry-jev-pilot-code-r1-P1.md` … `-P6.md` (28 findings). User decision: **all 28 fixed**; round 2 approved (final round).

- A (privacy/retention, fix): P1-F1 JSON secret assignments; P2-F1 re-check expiry before every JEV attempt; P2-F2 purge `--verify` must require successful checkpoints; P2-F3 backup physical cleanup retried (freelist-based VACUUM); P2-F4 busy live checkpoint is not a successful purge; P2-F5 unreadable backup must not block live retention; P4-F1 spool maintenance covers every due prompt (index), not only the first 50 files; P4-F2 transmission gate strips prompts when capture is not ready (queue and replay); P4-F3 failed writes leave no `.tmp`, recurring tmp maintenance.
- B (correctness, fix): P1-F3 descendant root reconciliation; P1-F4 `:memory:` init; P2-F6 malformed metadata → invalid_response; P3-F1 hidden project filter reset; P3-F2 unavailable prompt persisted as skip; P4-F4 short-write loop; P4-F5 malformed-ack shape + warnings.
- C (doc): P1-F2, P3-F3, P5-F1 — Drift Log said `known`; corrected to `child` (code unchanged).
- D (tests, add): P5-F2 independent schema expectations; P5-F3 capture cases over the batch endpoint; P5-F4 first-write keeps captured/expires; P6-F1 concurrent `/evaluate`; P6-F2 monthly ceiling + failed reserved cost; P6-F3 lifespan-level retention; P6-F4 label output blindness + deferred score; P6-F5 A/B detail race; P6-F6 children rendering.
