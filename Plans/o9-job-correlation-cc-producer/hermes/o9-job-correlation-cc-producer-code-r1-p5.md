# Hermes code review r1 — part 5 of 10

TAG: `o9-job-correlation-cc-producer-code-r1-p5` · prompt: backend code 5/5 — maintenance docs.

FINDINGS:
F1 [LOW] AGENTS.md — Jobs / “job_ref_conflicts (conflicting calls)” — This describes the stored task counter as a call count, but the accepted Phase 3 Drift Log explicitly says tasks.job_ref_conflicts also counts tool events; export conflicts instead count only counted snapshot calls. — scenario: a task assigned job A receives a tool event tagged job B → the stored counter increases without a conflicting LLM call, so following this maintenance guidance misinterprets the anomaly and export reconciliation. — fix: distinguish the stored conflicting-event counter from counted-call conflict metrics.
F2 [LOW] CLAUDE.md — Critical Technical Patterns; AGENTS.md — Gotchas / attribution_invalid — “Stored, never counted” overstates AC2.10, which specifies exclusion from jobs and the export, not every analytics or task total. README correctly limits its statement to jobs. — scenario: a maintainer uses these instructions to interpret a legacy analytics total containing an invalid-pair event → they conclude that the event cannot contribute, although the stated contract provides no such guarantee. — fix: explicitly say “excluded from jobs and the versioned export”; document other aggregation behavior only after checking its implementation.
UNSURE:
- Only maintenance-document diffs were supplied. Code correctness and actual AC/test coverage—including privacy canaries, dedup, snapshot stability, migration/rollback, repair transactions, hook concurrency/latency, and installer ownership/atomicity—cannot be verified from test names and documentation claims.
- The claim that global Claude Code hook changes affect every running session immediately is not established by the supplied hook evidence. Its reliability matters to the uninstall-before-rollback guidance.
- AC-DB1.3 requires refusing direct v12 → v10 rollback, whereas AC-DB3.5 explicitly permits the two-step runner operation documented in README. The later criterion appears to supersede the earlier one, but the supplied Drift Log does not explicitly reconcile them; this is not evidence of a runner defect.
- No non-placeholder remote address or credential token was observed in the supplied diff; loopback addresses are local examples. Unshown document content cannot be assessed.
- No repository inspection, tests, state-changing commands, or PEGA access were performed.
VERDICT: Two low-severity documentation corrections; this part alone cannot establish implementation or acceptance-test compliance.
