# Status — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Started**: 2026-09-28
**Last updated**: 2026-09-28 (plan LOCKED after hermes plan review r1; changes from here go to the Drift Log)
**Plan base commits**: backend PLAN_BASE = `904dec2` (backend HEAD at plan lock; the code diff excludes `Plans/`); plugin `4b069b4` (both on `feat/task-telemetry-jev-pilot`)
**Phases**: 1 = job correlation + cross-project child fix · 2 = Claude Code producer + dedup authority · 3 = versioned read-only export. Each phase ends at a checkpoint (commit + push in the repos it touched — never a deploy). Hermes code review and the Claude integration review run once, at the end; the single prod deploy follows them, with user approval (O1).

## Execution Plan
_Chosen at /new-plan Step 3c. Every executed step is run by Claude directly — **no handoffs** (`handoffs/` is not created)._

| Step | Executor | Model | Skill / plugin | Handoff |
|---|---|---|---|---|
| database | Claude Code (direct) | Opus 5.5 | — | no |
| backend (backend repo + plugin repo `C:\Users\Bentego_Admin\Projects\token_inspector` in Phase 1) | Claude Code (direct) | Opus 5.5 | — | no |
| frontend | Claude Code (direct) | Opus 5.5 | frontend-design | no |
| tests-other | Claude Code (direct) | Opus 5.5 | — | no |
| tests-e2e | Claude Code (direct) | Opus 5.5 | — (extends the existing Playwright browser suite, `-m browser`) | no |
| hermes plan review | Hermes (driven by Claude) | hermes default (never named) | — | no — 1 round (brief) |
| hermes code review | Hermes (driven by Claude) | hermes default (never named) | — | no — 1 round (brief); spec in `## Hermes Code Review
**Hermes code review** of **O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export** — run after the implementation lanes and the maintenance docs. One round only (user rule 2026-09-28). Hermes reviews; Claude drives it and applies the findings by default. The user is asked only for findings that conflict with a locked decision, open prod/deploy/flags, or grow scope; auth-heavy findings go to Known Limitations ("single user, local"). Every finding not applied is written under `## Known Limitations` with its reason. Hermes writes no code and changes no file.

### Content to review (split into parts, each prompt ≤ 100 KB)
- **Part 1 — backend code:** `git diff 904dec2 -- . ':!Plans' ':!tests'` in `tokenInspector`, plus the full text of new untracked non-test files (incl. `producers/claude_code/`, `scripts/rollback_schema.py`, the repair script, `docs/export-contract-v1.md`, new ADRs). Split further by directory if over 100 KB.
- **Part 2 — backend tests:** `git diff 904dec2 -- tests` plus new test files.
- **Part 3 — plugin + shared files:** `git diff 4b069b4` in `token_inspector` (plus new files), and the S1–S4 diffs of the shared files (three `hermes.sh` copies, `~/.claude/commands/hermes.md`). S5 (`settings.json`) is reviewed as installer code only — never the real file content.
- Every part also carries the brief's acceptance criteria, the Acceptance Criteria section of each lane file, and this file's Drift Log and Known Limitations (so accepted items are not re-raised).

### Review focus (the prompt's TASK)
Correctness bugs with a concrete failing scenario; every AC implemented and covered by a test; privacy (no prompt/response text, tool args, paths, hostnames or free error text in any payload, tag, export or state file; value-level canaries); dedup and double counting across producers; export snapshot/cursor stability; migration and rollback safety (v11/v12, `ingest_seq` high-water, `rollback_schema.py`); repair transaction safety; hook fail-open, the 5 s latency bound and concurrent-hook safety; installer ownership and atomic write; contract mismatches between lanes; deviations from the lane specs without a Drift Log entry.

### Protocol
The "Hermes review protocol" of `~/.claude/commands/new-plan.md` — script `C:/Users/Bentego_Admin/Projects/AIFromScratch/scripts/hermes.sh`, never a model or provider, READ-ONLY and "PEGA unreachable" rules in every prompt, `wait` in the background, a hermes queue row plus a message to PossibleSkills before and after, reports to `hermes/<TAG>.md` — with TAG = `o9-job-correlation-cc-producer-code-r1-p<N>` and deliverable = code, **except** its triage and round rules (steps 6–7): one round, findings applied by default as stated above. Fixes are verified with tests; a fix to a control also gets a mutation check (break → red → revert) logged in the Verification Log.

### When done
Set the `hermes-review` row in Lane Status to done, then run the Claude integration review (a new Opus subagent that did not write the code, read-only).

## Lane Status
_Tool / model / skill per step come from the execution plan chosen at /new-plan Step 3c. Claude steps run directly (no handoff); other tools use Plans/<slug>/handoffs/<lane>-<tool>.md._

| Lane | Tool (model, skill) | Status | Notes |
|---|---|---|---|
| database | Claude Code (Opus 5.5, direct) | not started | v11 in Phase 1 (`tasks.job_ref`, `job_ref_conflicts`), v12 in Phase 3 (`token_events.ingest_seq`). Backend repo only |
| backend | Claude Code (Opus 5.5, direct) | not started | Phase 1: J0 gate (AC1.1) first, then J1–J9 in backend + plugin repos; Phase 2: C0 gate (AC2.1) first, then C1–C11 in the backend repo; Phase 3: X1–X6. Shared files only via S1–S5 (SESSION-COORDINATION) |
| frontend | Claude Code (Opus 5.5, direct, frontend-design) | not started | Phase 1 Jobs view (AC1.6); Phase 3 Export panel (AC3.1, AC3.9). No Phase 2 work |
| tests-other | Claude Code (Opus 5.5, direct) | not started | Written with each backend step; mutation checks PJ*/BJ* (Phase 1), CM* (Phase 2), XM* (Phase 3). Suites locally and on hermes at every checkpoint |
| tests-e2e | Claude Code (Opus 5.5, direct) | not started | Playwright `-m browser`, own module-scoped servers; Phase 1 Jobs view, Phase 3 CSV download; mutations FM1–FM3. Run after the frontend work of the phase |
| hermes-review | Hermes (driven by Claude Code) | not started | After all three phases and tests-e2e, before review — spec in `## Hermes Code Review` (spec written at plan lock) |
| review | Claude Code (Opus 5.5, direct) | not started | Run last — full integration review (AC → test map across both repos, cross-repo contracts: env contract, tag keys, `parent_project_name`, attribution vectors, `valid_ack`; Deploy Runbook review) |

## Phase Checkpoints
_One row per phase. A phase is closed only when every column is filled. Mutation results link to the Verification Log tables._

| Phase | Local suite (backend / plugin / browser) | Hermes suite (backend / plugin: venv py + Hermes runtime py) | Mutation results | Backend commit(s) | Plugin commit(s) | Pushed to `hermes` | Date |
|---|---|---|---|---|---|---|---|
| 1 — job correlation + cross-project fix | | | PJ1–PJ8, BJ1–BJ26, FM1, FM4, FM8 | | | | |
| 2 — Claude Code producer + dedup authority | | | CM1–CM37 | | attribution-vector runner + fixture (B-F16) | | |
| 3 — versioned read-only export | | | XM1–XM25, FM2–FM3, FM5–FM7 | | — (no plugin change) | | |

A checkpoint is commit + push only; nothing is deployed, installed or restarted at a checkpoint.

## Drift Log
_Append here when any lane discovers the spec is wrong. Do not edit lane files mid-flight. The J0 verdict, the J2 measured tag size and cap, and every C0 rule are recorded here as they are decided._

- 2026-09-28 — Lane files written by the planner from the brief (incl. "Open questions resolved"). Planner decisions to confirm at the plan review: (1) task→job is a column on `tasks` (v11) so "a task belongs to at most one job" is enforceable, job context itself stays in tags; (2) the export snapshot uses a new `token_events.ingest_seq` (v12) because `recorded_at` is not commit-ordered and `rowid` is not VACUUM-stable; (3) CSV is built in the dashboard from the JSON export (no server CSV); (4) the Jobs view is a new nav view, the Export panel lives in it; (5) the env contract uses `TOKEN_INSPECTOR_JOB_REF` / `_WORK_TYPE` / `_JOB_ATTEMPT` and the skill passes `HERMES_WORK_TYPE` to `hermes.sh`; (6) CC producer modules are flat `cc_*.py` files so the hook runs as a plain script.

## Hermes Reviews
_Max 2 rounds per deliverable (plan, code). No round 3. Reports: Plans/o9-job-correlation-cc-producer/hermes/. This plan's brief sets 1 round for the plan and 1 for the code._

| Deliverable | Round | Date | Findings | Fixed | Known limitation | Rejected |
|---|---|---|---|---|---|---|
| plan | r1 (2 parts: A = summary + database + backend, B = summary + frontend + tests-other + tests-e2e + status) | 2026-09-28 | 41 (A 23, B 18) | 41 (32 fixed + 9 merged into their Part A twin) | 0 | 0 |

**Plan review r1 — per finding** (reports: `hermes/o9-job-correlation-cc-producer-plan-r1-A.md`, `-B.md`). Applied by default per the user's 2026-09-28 rule; overlaps resolved once, in the owning lane, and covered by the test lanes. No finding was left unapplied. New user decision created: O13 (hook latency bound).

| Finding | Severity | Outcome | Where |
|---|---|---|---|
| A-F1 | BLOCKER | fixed — snapshot job = first job tag among snapshot events; conflicts from snapshot events; mutations bump `revision` → `snapshot_expired` | backend.md X2, X5; database.md v12 "Revision and epoch"; tests-other `test_snapshot_job_membership_is_frozen`, `test_cursor_expires_on_mutation`, XM16/XM18 |
| A-F2 | HIGH | fixed — persistent high-water `export_state.last_seq` (never reused after deletes) | database.md v12 Schema/Assignment; backend.md X1; tests-other `test_ingest_seq_not_reused_after_delete`, XM17 |
| A-F3 | HIGH | fixed — group finality rule; checkpoint never passes a non-final group | backend.md C0 item 3, C2 item 3, C6; tests-other `test_streaming_group_split_across_reads`, CM21 |
| A-F4 | HIGH | fixed — per-batch group-safe checkpoint, send deadline inside the watchdog | backend.md C6, C7; tests-other `test_checkpoint_per_batch_progress`, CM22 |
| A-F5 | HIGH | fixed — truncation/replacement via size + file identity, oversized-line handling, bounded sibling drain of `pending` backlogs | backend.md C2 item 1, C5, C7; tests-other truncation/oversized/backlog tests, CM24–CM26 |
| A-F6 | HIGH | fixed — per-transcript lock (stale takeover), monotonic atomic checkpoint, unique temp files, lock-aware pruning | backend.md C5; tests-other `test_concurrent_hooks_state_safe`, CM27–CM28 |
| A-F7 | HIGH | fixed — cross-project unique index for `cc-` ids + untargeted `ON CONFLICT DO NOTHING` | database.md v11; backend.md C10 item 2; tests-other `test_cross_project_cc_dedup`, `test_cc_client_event_unique_across_projects`, CM30, BJ25 |
| A-F8 | HIGH | fixed — `attribution_invalid` mark; marked events stored but never counted; legacy inference only without runtime, producer and mark | backend.md J1, J5, C1, C10 item 1, X3, X4; tests-other `test_invalid_pair_not_counted_end_to_end`, BJ19, CM33, XM23 |
| A-F9 | HIGH | fixed — value rules for every emitted/exported string (safe-id/pseudonym, model shape, closed error class, strict job ref, CC ids pseudonymized, hostname check); single-label-hostname residual documented in the export contract | brief Key constraints; backend.md "Value rules", C3, C4, X4, X6; tests-other value-canary tests, CM31–CM32, XM20, PJ8 |
| A-F10 | HIGH | fixed — installer prints a content-free operation summary, never a diff; backup local only | backend.md C9, S5; status.md Deploy Runbook step 8; tests-other `test_installer_output_is_content_free`, CM34 |
| A-F11 | HIGH | fixed — exact-command ownership, mixed containers preserved, atomic write with unchanged-source check | backend.md C9; tests-other `test_installer_exact_ownership`, `test_installer_aborts_on_concurrent_change`, CM35–CM36 |
| A-F12 | HIGH | fixed — proposed graph before writes, cyclic / depth / unresolved classes refused, order-independent roots, postconditions, per-class counts | backend.md J7; tests-other `test_repair_graph_cases`, BJ23 |
| A-F13 | HIGH | fixed — backup verified against its own snapshot (before ≤ backup ≤ after), plan recomputed inside `BEGIN IMMEDIATE` | backend.md J7 `--apply`; tests-other `test_repair_revalidates_under_write_lock`, `test_repair_backup_verified_during_ingest`, BJ22, BJ24 |
| A-F14 | HIGH | fixed — change-detection promise removed: `updated_at` informational, full-replacement semantics | brief AC3.7; backend.md X4, X6; tests-other `test_stable_ids_and_updated_at` |
| A-F15 | HIGH | fixed — event export carries the canonical job + `job_ref_conflict` flag | backend.md X4; tests-other `test_event_job_ref_is_canonical`, XM22 |
| A-F16 | MEDIUM | fixed — task `runtimes`/`work_types` arrays with the jobs cardinality rule; `task_ref` absent (not null) | backend.md X4; tests-other `test_task_multi_value_fields`, `test_zero_unknown_absent_distinct` |
| A-F17 | MEDIUM | fixed — tasks/jobs named a start-time cohort; reconciliation limits documented; boundaries tested | brief AC3.3; backend.md X2, X6; tests-other `test_cohort_period_semantics`, XM21 |
| A-F18 | HIGH | fixed — J0 and C0 are stop-and-ask gates; admissible evidence named per fact | brief AC1.1, AC2.1, AC2.5; backend.md J0 step 4, C0, C8; status.md O2, O3 |
| A-F19 | MEDIUM | fixed — child `turn_id` = run id; error records mapped to `error`/`api_error`; calls without usage are a documented coverage limit | backend.md C0 item 7, C2 item 2, C3, C8; tests-other `test_child_turn_id_is_run_id`, `test_error_record_status`, CM37 |
| A-F20 | HIGH | fixed — `scripts/rollback_schema.py` with exact source-version guard, reverse order, SQLite ≥ 3.35 check, matching app artifact | database.md "Schema rollback runner", AC-DB1.3, AC-DB3.5; status.md Rollback; tests-other `test_rollback_runner_guards`, XM25 |
| A-F21 | MEDIUM | fixed — heal is an explicit step inside `init_db`'s migration transaction; Python-loop numbering needs only SQLite 3.24; versions split forward vs. rollback | database.md v12 "Backfill without window functions", "Self-heal"; tests-other `test_heal_is_atomic`, XM24 |
| A-F22 | MEDIUM | fixed — conflicts aggregated in a separate `tasks` subquery; filters select whole jobs after aggregation | backend.md J5; tests-other `test_conflicts_events_vs_tasks`, `test_jobs_filters_select_whole_jobs`, BJ20–BJ21 |
| A-F23 | MEDIUM | fixed — one final deploy (backend before plugin inside it); checkpoints commit + push only; hook latency bound defined (O13) | brief Key constraints, AC1.4, Recommended approach; backend.md J2, C7, checkpoints; status.md header, Phase Checkpoints, Deploy Runbook |
| B-F1 | HIGH | merged into A-F6 | backend.md C5; tests-other `test_concurrent_hooks_state_safe` (incl. pruning, termination) |
| B-F2 | HIGH | merged into A-F3 | backend.md C0 item 3, C2 item 3; tests-other `test_streaming_group_split_across_reads` |
| B-F3 | HIGH | fixed — rejected items: explicit deliberate-drop policy (deterministic failures), counted content-free; backend contract test expects zero rejects | backend.md C6; tests-other `test_rejected_items_dropped_and_counted`, `test_producer_events_never_rejected_by_backend`, CM23 |
| B-F4 | HIGH | merged into A-F4 + A-F5 (plus blocked-stdin / slow-IO bound) | backend.md C5, C7; tests-other `test_hook_bounded_on_blocked_stdin_and_slow_io`, CM29 |
| B-F5 | HIGH | merged into A-F7 | database.md v11; backend.md C10 |
| B-F6 | HIGH | merged into A-F8 | backend.md J1, C10, X4 |
| B-F7 | HIGH | merged into A-F1 | backend.md X2; database.md "Revision and epoch" |
| B-F8 | HIGH | merged into A-F9 | backend.md "Value rules"; tests-other value canaries convention |
| B-F9 | HIGH | merged into A-F2 (+ own end-to-end test and cursor lifetime rule) | database.md AC-DB3.6, "Revision and epoch" (epoch); tests-other `test_v10_v12_old_code_round_trip` |
| B-F10 | HIGH | merged into A-F12 + A-F13 (plus failure atomicity) | backend.md J7; tests-other `test_repair_failure_is_atomic` |
| B-F11 | MEDIUM | fixed — synthetic execution checks for all three launcher copies, `devir-baslat` and the skill; live checks tracked per copy | tests-other `test_launcher_env_contract`, BJ26; status.md Deploy Runbook step 9, Verification Log |
| B-F12 | MEDIUM | fixed — Jobs request generation + abort; export run id with frozen params, stop on supersede, repeated/missing cursor, page failure, row cap | frontend.md State Management, CSV step 1, AC1.6-e, AC3.4 (UI); tests-e2e race/termination rows, FM4, FM6, FM7 |
| B-F13 | MEDIUM | fixed — multi-page Jobs (intercepted), two-page export, tab/CR guard, partial badge, mixed work type, empty export, exact boundaries | tests-e2e seed data and Phase 1/3 rows |
| B-F14 | MEDIUM | fixed — closed code→message map, generic fallback for unknown/non-JSON/network | frontend.md Export status line, AC3.3 (UI); tests-e2e closed error policy row, FM5 |
| B-F15 | MEDIUM | fixed — separate conflicting-call and affected-task counts in API and UI wording | backend.md J5; frontend.md anomaly line, Conflicts column, AC1.6-c; tests-other `test_conflicts_events_vs_tasks`; tests-e2e AC1.6-c, FM8 |
| B-F16 | MEDIUM | fixed — Phase 2 plugin attribution-vector runner in checkpoint ownership; cross-repo comparison must run (not skip) at the integration review | backend.md Phase 2 intro, checkpoint; tests-other Phase 2 files; status.md Phase Checkpoints |
| B-F17 | MEDIUM | fixed — `test_hook_silent_on_success` added and CM10 retargeted; mutation evidence must name the failing assertion | tests-other conventions, AC2.7 rows, CM10 |
| B-F18 | MEDIUM | fixed — PLAN_BASE (O11), Hermes Code Review spec (O12) and O5 made blocking; stale "pending confirmation" and per-phase deploy wording removed (values left for the orchestrator) | status.md header, Lane Status, Open Items O5/O11/O12, Deploy Runbook; brief AC1.8, Open questions; backend.md S1, J8 |

## Hermes Code Review
_Placeholder — the orchestrator writes this spec at /new-plan Step 7b, with PLAN_BASE (backend) and `4b069b4` (plugin) as the diff bases. Scope must cover both repos, the new `producers/claude_code/` tree, the migrations, and the coordinated shared-file diffs (S1–S5)._

## Deploy Runbook
_Owner: Claude executes; the user approves the deploy as a whole and, separately, the gateway restart, the hook installer on each machine and any repair `--apply`. Follows the O10 runbook (Plans/prompt-allowlist-safe-producer/status.md). No capture flag is enabled and `INGEST_TOKEN` stays unset. Addresses and tokens are never written here: use `$TI_URL` / env._

Upgrade — the **single** prod deploy, after the Claude integration review, with user approval (O1). Nothing below runs at a phase checkpoint.

1. **Flags-off check.** On hermes: backend `STORE_TASK_PROMPTS` and `JEV_ENABLED` off, `secrets.env` sets no capture flag, `INGEST_TOKEN` unset; plugin `capture_task_prompt` off. Note the prod commits (backend `main`, installed plugin).
2. **Online DB backup + verify.** `sqlite3` online backup API (not a file copy) of the prod `DB_PATH` to `~/backups/token-inspector-pre-o9-<UTC stamp>.db`; `PRAGMA integrity_check == ok`; `token_events` and `tasks` counts equal the source. Never commit or mirror it.
3. **Backend fast-forward `main`.** On hermes, prod checkout: fetch the pushed branch and `git merge --ff-only` it into `main`. Schema changes v10 → v11 → v12 run at startup with the app's automatic, verified pre-migration backup (`<db>.bak-v10-*`); confirm it exists after the restart. Dependencies: expect no change (`requirements.txt` diff empty; the CC producer is stdlib-only); if the diff is not empty, install into the service venv before restarting.
4. **Service restart + `/api/meta`.** `systemctl --user restart token-inspector.service`; `curl -fsS <loopback>/api/meta` → `schema_version: 12`, capture/JEV `not_enabled`, `task_prompt_allowlist: "empty"`; journal clean; `GET /api/jobs?days=1` → 200; `GET /api/export/v1/events?from=…&to=…` → 200 with `schema_version: 1`. The backend with the new tag cap is now live **before** any plugin that sends the new keys (AC1.4).
5. **Repair script dry-run.** `python scripts/repair_task_parents.py --db "$DB_PATH"` → show the counts line (one count per class, incl. `cyclic`, `depth_exceeded`, `unresolved_ancestor`) to the user. `--apply` only with a **separate** user approval; it takes and verifies its own backup first. Record counts (no refs) in the Verification Log.
6. **Coordinated shared files** S1–S4 (three `hermes.sh` copies, `/hermes` skill) applied through SESSION-COORDINATION, if not already done.
7. **Plugin install + `hermes gateway restart`** only when no agent or JEV run is active: no `hermes -z` process, `evaluator_runs` has no `queued`/`running` row, the "hermes sırası" table shows no running job. Update the plugin source and the installed copy to the pushed commit, then `hermes gateway restart` from an external shell. Record PIDs before/after.
8. **Hook installer (S5)** on Windows and on hermes: `python producers/claude_code/install.py --dry-run` → show its content-free operation summary (never a settings diff) → announce in SESSION-COORDINATION → `--apply` (local backup written, never synced; atomic write aborts if another session changed the file). Windows: the producer URL comes from env or the local config file (the tailnet HTTPS name allowed by the `remote-access.conf` drop-in), never from docs. hermes: loopback default. Confirm PossibleSkills' hooks are unchanged in the resulting file.
9. **Final check** (Verification Log; every live row stays "pending deploy" until observed, each tracked separately — B-F11):
   - AC1.2 live, per launcher copy that TI can exercise: one small `hermes.sh` review job through the `/hermes` flow (queue rule) with the AIFromScratch copy → `GET /api/jobs?days=1` lists its `job_ref` with `runtime=hermes-agent`, `work_type=review`. The PEGADocRag / PEGADocRagAgent `send` copies are covered by `test_launcher_env_contract` (synthetic) and stay "pending first real run" (no PEGA access needed here).
   - AC2.2 live: one Windows Claude Code turn → export events of the last hour include `runtime=claude-code@windows`, `producer=claude-code-hook`, the right project, distinct cache fields.
   - AC2.3 live: the next real devir run (or a short one the user approves) → `runtime=claude-code@hermes`, `work_type=devir`, `job_ref` = the devir id, project `pegadocrag`; no prompt field.
   - `tasks.prompt_text` non-null count unchanged (0); backend journal clean; plugin `counters.json` shows no new rejects.

Rollback (either part):
1. Flags stay off (they were never turned on). Uninstall the CC hooks first if they misbehave — and **always** before a backend code-only rollback below the Phase 2 code: `install.py --uninstall --apply` on each machine (backup written).
2. Plugin before backend if both: restore the previous plugin commit, `hermes gateway restart` with no job running. The `hermes.sh` env lines are harmless without the plugin and can stay.
3. Backend: code-only rollback to the previous `main` commit is safe on a v12 DB (new columns nullable, partial indexes, `export_state` ignored); a later re-upgrade self-heals NULL `ingest_seq` above the high-water. Schema rollback only through `python scripts/rollback_schema.py --db "$DB_PATH" --to 11|10` (exact source-version guard, reverse order, SQLite ≥ 3.35), with the service stopped and only if needed, then start the app commit the runner names for the resulting version; restore from the step-2 backup as the last resort.

## Known Limitations
_Review findings the user chose not to fix — one line each: `<plan|code>-review r<N> F<k>: <what> — <why accepted>`._

- brief (resolved before planning): unauthenticated ingest from Windows until the Activation Gate — the CC producer posts over the tailnet without `X-Ingest-Token` while `INGEST_TOKEN` is unset; accepted: single user, local service, tailnet only, "auth minimal" rule; AC2.8 makes the producer ready for a token.

## Open Items
| # | Item | Owner | Blocks |
|---|---|---|---|
| O1 | Resolved (user goal, 2026-09-28): deploy **once**, after the Claude integration review (as O10), with user approval. Live checks AC1.2, AC2.2, AC2.3 are recorded as "pending deploy" until then | — | Live AC checks |
| O2 | J0 / AC1.1 gate: does `hermes -z` load plugins in its own process? **Stop-and-ask** if no, if the evidence is missing or inconclusive, or if a finding contradicts an AC (default proposal for "no": launcher-posted job→session mapping record) — never automatic drift | Claude (read-only), then user | Phase 1 J1+ |
| O3 | C0 / AC2.1 gate: CC hook stdin fields, subagent layout, parent link and child id, streaming duplicates and finality rule, resumed copies, `<synthetic>` lines, error records, line sizes, in-place rewrites — rules into the Drift Log. **Stop-and-ask** when a fact lacks admissible evidence (backend.md C0) or contradicts an AC | Claude (read-only), then user | Phase 2 C1+ |
| O4 | Tag byte cap value after the J2 measurement (default 1024, keys ≤ 20); ask if the measurement exceeds 768 B | Claude → orchestrator | J2 |
| O5 | Resolved (user, 2026-09-28): TI owns `AIFromScratch\scripts\hermes.sh` and `~/.claude/commands/hermes.md`; ledger rows added. TI edits them with a ledger line and a message to PossibleSkills | — | — |
| O6 | Owner agreement for S2, S3 (PEGA `hermes.sh` copies), S4 (`/hermes` skill) and S5 (`~/.claude/settings.json` on both machines, coexisting with PossibleSkills' planned PreToolUse hook) | Owners / user | Phase 1 live, Phase 2 deploy |
| O7 | Pricing rules or aliases for the Claude models Claude Code uses; until added they stay `unpriced` (never zero). Fix through the settings API + recost dry-run, not code | User | Complete CC cost |
| O8 | Repair `--apply` on prod after the dry-run counts | User (separate approval) | AC1.8 on prod data |
| O9 | NotebookLM: after the docs steps (incl. the tailnet-address placeholder in CLAUDE.md RP 12 and AGENTS.md gotchas) run `/sync-docs → /compress → /sync-memory`; the notebook is stale until then | Claude | — |
| O10 | PossibleSkills is told when the export is live (it does not connect before); contract = `docs/export-contract-v1.md` | TI → PS | PS export consumption |
| O11 | Resolved: PLAN_BASE `904dec2` recorded in the header | — | — |
| O12 | Resolved: `## Hermes Code Review` spec written | — | — |
| O13 | Resolved (user, 2026-09-28): `HOOK_BOUND_S = 5` s per invocation, 10 s hook `timeout` backstop | — | — |

## Verification Log
| Date | Check | Result |
|---|---|---|
| — | AC1.2 live (AIFromScratch `hermes.sh` review job) | pending deploy |
| — | AC1.2 PEGADocRag `send` / PEGADocRagAgent `send` (synthetic `test_launcher_env_contract` per copy; live on first real run) | pending (synthetic before review; live pending deploy) |
| — | AC2.2 live (Windows Claude Code turn) | pending deploy |
| — | AC2.3 live (devir run, `devir-baslat`) | pending deploy |

Mutation checks (one mutation at a time: break the control → named test red → revert; a survivor is a missing test unless marked redundant):

| # | Phase | Control | Result | Failing test(s) |
|---|---|---|---|---|
