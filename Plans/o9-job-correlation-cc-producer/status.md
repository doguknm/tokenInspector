# Status — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Started**: 2026-09-28
**Last updated**: 2026-10-01 (Phase 3 checkpoint: backend `c4a07bc` + status commit; next stage 2: hermes code review r1 + Claude integration review)
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
| database | Claude Code (Opus 5.5, direct) | done (P1 `e1cab32`; P3 v12 `2f5a761`) | v11 in Phase 1 (`tasks.job_ref`, `job_ref_conflicts`), v12 in Phase 3 (`token_events.ingest_seq`). Backend repo only |
| backend | Claude Code (Opus 5.5, direct) | done — Phase 1 + 2 (P2: `6ec9922`, `58b9753`, `f289af4` / plugin `bbe0781`) + Phase 3 (`2f5a761`, `cf4b1bf`, `c4a07bc`) | Phase 1: J0 gate (AC1.1) first, then J1–J9 in backend + plugin repos; Phase 2: C0 gate (AC2.1) first, then C1–C11 in the backend repo; Phase 3: X1–X6. Shared files only via S1–S5 (SESSION-COORDINATION) |
| frontend | Claude Code (Opus 5.5, direct, frontend-design) | done (P1 Jobs view `e1cab32`; P3 Export panel `be86f18`) | Phase 1 Jobs view (AC1.6); Phase 3 Export panel (AC3.1, AC3.9). No Phase 2 work |
| tests-other | Claude Code (Opus 5.5, direct) | done (P2 CM1–CM37: 37/37; P3 XM1–XM25: 25/25 caught) | Written with each backend step; mutation checks PJ*/BJ* (Phase 1), CM* (Phase 2), XM* (Phase 3). Suites locally and on hermes at every checkpoint |
| tests-e2e | Claude Code (Opus 5.5, direct) | done (P1 `e1cab32`; P3 `be86f18`, FM2–FM3, FM5–FM7: 5/5 caught) | Playwright `-m browser`, own module-scoped servers; Phase 1 Jobs view, Phase 3 CSV download; mutations FM1–FM3. Run after the frontend work of the phase |
| hermes-review | Hermes (driven by Claude Code) | done (r1, 10 parts, 32 findings: 25 fixed, 7 known limitation; `f1ad51d`, `8de894f`, `006451d` / plugin `cb56106`) | After all three phases and tests-e2e, before review — spec in `## Hermes Code Review` (spec written at plan lock) |
| review | Claude Code (Opus 5.5, direct) | not started | Run last — full integration review (AC → test map across both repos, cross-repo contracts: env contract, tag keys, `parent_project_name`, attribution vectors, `valid_ack`; Deploy Runbook review) |

## Phase Checkpoints
_One row per phase. A phase is closed only when every column is filled. Mutation results link to the Verification Log tables._

| Phase | Local suite (backend / plugin / browser) | Hermes suite (backend / plugin: venv py + Hermes runtime py) | Mutation results | Backend commit(s) | Plugin commit(s) | Pushed to `hermes` | Date |
|---|---|---|---|---|---|---|---|
| 1 — job correlation + cross-project fix | backend **388 passed** (incl. 26 browser) / plugin **196 passed, 2 skipped** | backend **347 passed, 16 skipped** (browser, no Playwright) / plugin **198 passed** on Python 3.12.3 and Hermes runtime 3.11.15 (temp worktrees, removed; prod checkouts on `main`) | PJ1–PJ8, BJ1–BJ26, FM1, FM4, FM8: **37/37 caught** | `e1cab32` | `6b79ec9` | yes (both; backend also `origin`) | 2026-09-28 |
| 2 — Claude Code producer + dedup authority | backend **488 passed** (incl. 26 browser) / plugin **199 passed, 2 skipped** | backend **448 passed, 15 skipped** (after one rerun, see Verification Log) / plugin **201 passed** on Python 3.12.3 and Hermes runtime 3.11.15 (temp worktrees under `/tmp/o9-p2/`, removed; prod checkouts on `main`) | CM1–CM37: **37/37 caught** | `31538a8` (C0), `6ec9922`, `58b9753`, `f289af4` + status commit | `bbe0781` (attribution-vector runner + fixture, B-F16) | yes (both; backend `origin` tried) | 2026-09-28 |
| 3 — versioned read-only export | backend **535 passed** (incl. 41 browser) / plugin — (unchanged) | backend **478 passed, 17 skipped** (browser, no Playwright) on Python 3.12.3 (temp worktree `/tmp/o9-p3/`, removed; prod checkout on `main`); repair test **10/10** standalone | XM1–XM25, FM2–FM3, FM5–FM7: **30/30 caught** | `fdbb1e5` (flaky test), `2f5a761` (v12, X1), `cf4b1bf` (X2–X5), `be86f18` (Export panel + browser test), `c4a07bc` (X6 docs) + status commit | — (no plugin change) | yes (backend `hermes`; `origin` tried: network failure to github.com) | 2026-10-01 |

A checkpoint is commit + push only; nothing is deployed, installed or restarted at a checkpoint.

## Drift Log
_Append here when any lane discovers the spec is wrong. Do not edit lane files mid-flight. The J0 verdict, the J2 measured tag size and cap, and every C0 rule are recorded here as they are decided._

- 2026-09-28 — Lane files written by the planner from the brief (incl. "Open questions resolved"). Planner decisions to confirm at the plan review: (1) task→job is a column on `tasks` (v11) so "a task belongs to at most one job" is enforceable, job context itself stays in tags; (2) the export snapshot uses a new `token_events.ingest_seq` (v12) because `recorded_at` is not commit-ordered and `rowid` is not VACUUM-stable; (3) CSV is built in the dashboard from the JSON export (no server CSV); (4) the Jobs view is a new nav view, the Export panel lives in it; (5) the env contract uses `TOKEN_INSPECTOR_JOB_REF` / `_WORK_TYPE` / `_JOB_ATTEMPT` and the skill passes `HERMES_WORK_TYPE` to `hermes.sh`; (6) CC producer modules are flat `cc_*.py` files so the hook runs as a plain script.
- 2026-09-28 — **J0 verdict: YES** (`hermes -z` discovers plugins and builds its `AIAgent` in its own process; source trace in the Verification Log). Phase 1 continues as planned; no fallback needed.
- 2026-09-28 — **User decisions (~22:00, recorded by the orchestrator):** stop tonight after the Phase 2 checkpoint; Phase 3 starts in the next session, sequentially; the single prod deploy stays; S2/S3 patches are applied by TI on deploy day with the user's approval (O6, runbook 6a). (The Phase 2 subagent's attempt to write these was correctly blocked: relayed agent messages are not user consent.)
- 2026-09-28 — **Open (Phase 2 report):** `test_repair_backup_accepts_real_concurrent_writer` (Phase 1) is flaky on hermes (1 of 5 standalone runs failed; full rerun passed). To fix before the hermes code review (test timing, not the control).
- 2026-09-28 — **J2 measurement:** worst-case plugin llm-event tags incl. the new keys = **17 keys, 722 bytes** (`tests/fixtures/worst_case_plugin_tags.json`, pinned by `test_worst_case_plugin_tags_accepted`); below the 768 B ask threshold, so `TAG_BYTES_MAX = 1024` (keys ≤ 20) as planned (O4 resolved).
- 2026-09-28 — **Phase 1 checkpoint taken by the orchestrator:** the Phase 1 subagent was stopped four times (API session limit twice, network outage twice, then the session ended) before committing. The orchestrator verified that no mutation was left applied (all 36 runner `old` strings present), ran the suites, committed, ran the remaining mutations (BJ24, BJ25, FM1, FM4, FM8 via the runner; BJ26 by hand on a temp launcher copy) and the hermes suites. Plugin tests on hermes need the worktree directory to be named `token_inspector` (conftest imports the package by that name).
- 2026-09-28 — **Note for Phase 2 (C9; not implemented in Phase 1):** PossibleSkills installs its `settings.json` hooks under an OS lock file `settings.json.lock-possibleskills` next to the settings file. The C9 installer should take the same lock for the whole `--apply` / `--uninstall --apply` (in addition to its unchanged-source hash check), so the two installers never write concurrently. Source: coordinator message 2026-09-28.
- 2026-09-28 — **C0 / AC2.1 gate: PASS** (evidence in the Verification Log; no AC contradicted, every fact has admissible evidence). The C0 rules below refine backend.md C2/C3/C5/C8 within the ACs and are what Phase 2 implements:
  - **C0 rule 1 (hook stdin).** Stop: `session_id, prompt_id, transcript_path, cwd, permission_mode, hook_event_name, stop_hook_active, last_assistant_message, background_tasks, session_crons`; SubagentStop: the same plus `agent_id, agent_type, agent_transcript_path`; SessionEnd: `session_id, prompt_id, transcript_path, cwd, hook_event_name, reason`. `session_id` equals the transcript file stem. Stdin is UTF-8 (non-ASCII profile-path bytes decode). The hook reads only `hook_event_name`, `transcript_path`, `agent_transcript_path` and `cwd` (the last only locally for attribution); `last_assistant_message`, `background_tasks`, `session_crons`, `prompt_id` and `agent_type` are never read.
  - **C0 rule 2 (subagents).** Each subagent run is its own file `<project dir>/<session_id>/subagents/agent-<agentId>.jsonl` (a `.json` sidecar next to it is never read); every line carries `agentId` (= file suffix), `sessionId` (= the parent session) and `isSidechain: true`. Child `session_id` = `turn_id` = pseudonym of `sessionId + "\x1f" + agentId` (agent ids are 17 hex chars; scoping them by session makes collisions impossible). **Parent link:** the first `promptId` in the subagent file equals the `promptId` of the parent's Agent tool-result line (60/60), i.e. the parent prompt in effect when the subagent started → `parent_turn_id` = pseudonym of that `promptId` (same key as C0 rule 6), `parent_session_id` = pseudonym of `sessionId`. A subagent file with no `promptId` is not sent (records counted `skipped_records`; never sent unlinked). Forked subagents (`fork-context-ref` first line) follow the same rule.
  - **C0 rule 3 (streaming groups, finality).** 1–9+ lines per `message.id`, always a single `requestId` per group; groups never interleave with other assistant groups, but tool-result (1406) and meta (29) user lines can sit **between** lines of one group (a new prompt line never does). Usage is identical across a group's lines in 97.7 % of groups; where it differs (437 groups) only the last line(s) carry a non-null `stop_reason`, and every line with a non-null `stop_reason` carries the group's final usage (33 895/33 895); the last line always has the maximum `output_tokens`. **Finality rule:** a group is final iff (a) one of its lines has a non-null `message.stop_reason`, or (b) a later assistant line with another `message.id` was read, or (c) a later new-prompt user line (not a tool result, not `isMeta`, not `isCompactSummary`) was read. The kept record is the group's last line read. **EOF on a hook never finalizes a group** (replaces backend.md C2 item 3 (b); the hooks doc says the transcript may lag at hook time). Scratch run: at all 3 Stop, 1 SubagentStop and 2 SessionEnd hooks every group on disk was already final (0 non-final, no partial last line), so in Claude Code 2.1.284 a turn's calls go out with that turn's Stop hook; on a lagging version the last call goes out with the next hook (deferred, never early). A null-`stop_reason` group that is the last group of a file (12 of 18 640, interrupted calls) is sent only when the session continues — named as a coverage limit in the producer README.
  - **C0 rule 4 (resumed copies).** `claude -p --resume` keeps the session id and appends to the same file (same file id, prefix unchanged). Other resumes copy earlier messages into another session's file with the same `message.id` and `requestId` (55 ids across two main files, 62/62 with one `requestId`); 7 ids occur in a main file and one of its forked subagent files. The id rule `cc-` + sha256(`message.id` ␟ `requestId`) counts each once; the first arrival owns project and task.
  - **C0 rule 5 (`<synthetic>`).** 588 lines, all with `message.id` and all-zero usage; 584 carry `isApiErrorMessage: true`. Skipped, never sent; those with `isApiErrorMessage` are counted `records_without_usage`.
  - **C0 rule 6 (turn boundary).** Turn key = the `promptId` of the most recent user line carrying one (prompt, tool-result or meta line; the hook doc's "prompt currently being processed"); a prompt line without `promptId` (older versions, 157 lines) falls back to its `uuid`. Tool-result lines carry the same `promptId` as the turn of the call that issued the tool use (16 851/16 854). `turn_id` = pseudonym of that key; the state's `turn_uuid` field stores the key in effect at the checkpoint. Refines C2 item 4 (line `uuid` → `promptId`), which also makes the C8 parent link exact.
  - **C0 rule 7 (error records).** API errors appear only as `<synthetic>` lines with zero usage → not measured (`records_without_usage`). Interrupted calls are real-model lines with `isAbortedMidStream: true` and usage (7) → sent with `status="error"`, `error_type="api_error"`; the same applies to a non-synthetic line with `isApiErrorMessage: true` (none observed). Assistant lines without `message.id`: none observed (still skipped + counted).
  - **C0 rule 8 (sizes).** Largest line 1.35 MB (main) / 0.89 MB (subagent); largest assistant usage line 126 KB → the 4 MiB window and `OVERSIZED_LINE_MAX = 32 MiB` stand.
  - **C0 rule 9 (append-only).** Two snapshots of 194 transcripts: no file shrank or changed identity, every grown file kept its prefix hash; `--resume` appended in place; compaction appends a `compact_boundary` line in the same file; `/clear` starts a new session file (SessionEnd `reason`). `st_ino` is non-zero on NTFS for all 194 files, so the C5 size + file-identity check applies unchanged on Windows.
  - Value shapes seen (counts only): all 38 088 real-model lines match `^claude-[a-z0-9.-]{1,64}$`, carry a `requestId` and a timezone-qualified `timestamp`; every non-null `stop_reason` matches `^[a-z_]{1,32}$`.
- 2026-09-28 — **Phase 2 implementation deviations** (all within the ACs; each covered by a test):
  - C5 state schema: a state file also holds `ctx` = the resolved project name + source and the valid job keys (ids / closed shapes; no path, content or token). Reason: the C7 sibling drain must send a backlog with the context of the session that wrote it, not with the draining hook's project or job env (a devir backlog drained by an interactive session would otherwise get the wrong `job_ref`). The drain processes a sibling transcript only when its state holds a ctx; the own session's `subagents/` files use the hook's own ctx. `test_state_holds_only_offsets_and_ids` pins the key set.
  - C5 pruning orders state files by file mtime (= the write time of `updated_at`) instead of parsing every file.
  - C3 `tool_call_count` = distinct `tool_use` ids over all lines of the group (C0: one content block per line), not only the kept line.
  - C2 anti-stall: when the only open group starts at the window start and is not final at the byte/record limit, the window extends until it is final (at most `OVERSIZED_LINE_MAX`, then it is taken as final), so a group interleaved with large tool results can never stall the cursor.
  - C2/C8: subagent records closed while no `promptId` of that file has been read are skipped (`skipped_records`), never sent unlinked.
  - C10 pairing is symmetric: a `producer` without its `runtime` is dropped and marked like a runtime without its producer. Two Phase 1 vectors of `test_reserved_keys_normalization` changed accordingly (Verification Log); no privacy assertion relaxed.
  - C7: the hook exits through `sys.exit(main())` (not `os._exit`) so buffered output can never be dropped silently (makes CM10 observable); the watchdog still hard-exits with `os._exit(0)`. After a watchdog kill the lock stays until it is stale (2 × `HOOK_BOUND_S`), so that transcript waits up to 10 s.
  - C9: the PossibleSkills lock follows their protocol exactly (open `settings.json.lock-possibleskills` `a+b`; `msvcrt.locking` on byte 0 on Windows, `fcntl.flock` elsewhere; 10 s wait, then exit 1 with a fixed message). The summary also prints the resolved settings path (no content).
  - C11 docs: ADR-004, `producers/claude_code/README.md` and the CI `py_compile` line are done; AGENTS.md (Coverage, module map, CC attribution order), README, ARCHITECTURE, CHANGELOG, CONTRIBUTING and the project CLAUDE.md RP move to the maintenance-docs step, like the Phase 1 J9 docs (AC2.11's AGENTS.md part is pending there).
- 2026-10-01 — **Flaky repair test closed** (`fdbb1e5`): the writer loop pauses 1 ms between commits; repair script and assertion unchanged; 10/10 standalone on hermes (Verification Log).
- 2026-10-01 — **Phase 3 implementation choices** (within the ACs; each covered by a test; nothing unspecified was added):
  - X2 snapshot job: the first job tag among the task's snapshot events of **any** task-turn type (not only counted calls), so it equals `tasks.job_ref` once every event is in the snapshot, as backend.md X2 states.
  - X2/X4 conflicts (`conflict_count`, `conflict_task_count`, task `conflict_count`) are computed over **counted** snapshot calls ("conflicting call"), not from `tasks.job_ref_conflicts` (which also counts tool events).
  - X3 staleness reads all snapshot events of any event type (evaluator and `attribution_invalid` excluded); coverage reads counted calls in the period.
  - X5 revision/epoch check on a later page runs **after** the page's reads, so a recost or repair committed before or during the page always returns 409 instead of a mixed page.
  - X4 value rules not named in backend.md: `provider` uses the safe-id rule (`p-` pseudonym otherwise); closed lists `role` = `primary`, `subagent`, `evaluator` (else `other`), `hierarchy_status` = `root`, `child`, `unknown`, `complexity_method` = `request-shape-v1` (else absent). A job row whose `job_ref` fails `JOB_REF_RE` is skipped (stored refs are already normalized). All documented in `docs/export-contract-v1.md`.
  - Frontend: `from >= to` (or an empty date) shows the closed message "Export failed: invalid range" (the closed set has no separate text for it); "Range must be at most 92 days" for a longer span. The row cap stops when 100 000 rows were fetched and more remain, so no request is made past the cap.
  - Tests: `test_v10_v12_old_code_round_trip` calls `routes.export._export` with a session on its own temp DB (the app engine is bound to the suite DB); `test_cursor_ignores_rows_after_delete_and_reingest` holds the cursor half of AC-DB3.3; `test_invalid_pair_not_counted_end_to_end` (Phase 2) gained export assertions for XM23.

## Hermes Reviews
_Max 2 rounds per deliverable (plan, code). No round 3. Reports: Plans/o9-job-correlation-cc-producer/hermes/. This plan's brief sets 1 round for the plan and 1 for the code._

| Deliverable | Round | Date | Findings | Fixed | Known limitation | Rejected |
|---|---|---|---|---|---|---|
| plan | r1 (2 parts: A = summary + database + backend, B = summary + frontend + tests-other + tests-e2e + status) | 2026-09-28 | 41 (A 23, B 18) | 41 (32 fixed + 9 merged into their Part A twin) | 0 | 0 |
| code | r1 (10 parts: p1–p5 backend code, p6–p9 backend tests, p10 plugin + S1–S4) | 2026-10-01 / 10-02 | 32 (7 HIGH, 21 MEDIUM, 4 LOW; no BLOCKER) | 25 | 7 | 0 |

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

**Code review r1 — per finding** (reports: `hermes/o9-job-correlation-cc-producer-code-r1-p1.md` … `-p10.md`; triage sheets `hermes/code-r1-triage-p1-p6.md`, `hermes/code-r1-triage-p7-p10.md`). Triage rule B: every finding was presented to the user; the user approved the recommendations (p1–p6 on 2026-10-01, p7–p10 with the instruction to finish the goal on 2026-10-02). p7 failed once with `HTTP 429` on hermes and was resent the next day (not a round). The fixes of groups G3–G5 and p7–p10 were implemented by hermes (code job `20261002-182356-141472`, temporary worktrees), then checked, tested and mutation-checked here; per-group Claude reviews were skipped on the user's instruction (2026-10-02).

| Finding | Severity | Outcome | Where |
|---|---|---|---|
| p1-F1 | HIGH | known limitation — model shape rule admits path-/host-like strings; contract residual extended | `docs/export-contract-v1.md` Residual; Known Limitations |
| p1-F2 | HIGH | fixed (`f1ad51d`) — `/api/jobs` re-applies job ref / runtime / work-type rules on read | `routes/jobs.py`; `test_jobs_legacy_tag_values_never_returned`; RM1–RM3 |
| p1-F3 | MEDIUM | fixed (`f1ad51d`) — limit length cap, cursor ints bounded to int64 | `routes/export.py`; `test_export_oversized_numbers_rejected`; RM4–RM6 |
| p2-F1 | HIGH | known limitation — merged with p1-F1 (error_type is any identifier-shaped class name) | as p1-F1 |
| p2-F2 | MEDIUM | fixed (`8de894f`) — repair backup created exclusively, suffix on a taken name | `scripts/repair_task_parents.py`; `test_repair_backup_never_overwritten_in_same_second`; RM7 |
| p2-F3 | MEDIUM | known limitation — dashboard export has no 30 s page timeout; a new run supersedes a stalled one | Known Limitations |
| p2-F4 | LOW | fixed (`006451d`) — contract `role` list includes `evaluator` | `docs/export-contract-v1.md`; `test_export_contract_documented` |
| p3-F1 | HIGH | known limitation — stale-lock takeover race; bounded by the monotonic checkpoint and backend dedup | Known Limitations |
| p3-F2 | HIGH | known limitation — a failed-delivery backlog resumed under another job is sent with the new job | Known Limitations |
| p3-F3 | MEDIUM | fixed (`8de894f`, `006451d`) — installer backup created exclusively; a partial backup is removed after a failed write | `install.py`; `test_installer_backup_never_overwritten_in_same_second`, `test_installer_removes_partial_backup_after_write_failure`; RM8, RM10 |
| p3-F4 | MEDIUM | fixed (`006451d`) — installer refuses a hook path with shell metacharacters | `install.py`; `test_installer_rejects_shell_metacharacters_in_hook_path`; RM9 |
| p3-F5 | MEDIUM | fixed (`006451d`) — early watchdog during imports; a failing import exits 0 silently | `cc_hook.py`; `test_hook_import_failure_is_silent_and_fail_open`; RM11 |
| p4-F1 | HIGH | fixed (`006451d`) — `finish_reason` closed list, else omitted | `cc_events.py`; `test_model_and_finish_reason_value_rules`; RM12 |
| p4-F2 | MEDIUM | fixed (`006451d`) — normalized host names excluded | `cc_attribution.py`; `test_normalized_hostname_git_root_falls_through`; RM13 |
| p4-F3 | MEDIUM | known limitation — `tool_call_count` can be low when a group is split after its final-usage line | Known Limitations |
| p5-F1 | LOW | fixed (`006451d`) — AGENTS.md: conflicting events (calls and tool events) | AGENTS.md Jobs |
| p5-F2 | LOW | fixed (`006451d`) — `attribution_invalid`: excluded from jobs and the export | CLAUDE.md, AGENTS.md |
| p6-F1 | MEDIUM | fixed (`006451d`) — FakeServer marks only accepted ids as seen | `tests/cc_support.py`; `test_rejected_unseen_id_can_be_inserted_on_retry` |
| p6-F2 | MEDIUM | fixed (`006451d`) — production 5 s watchdog test without overrides | `test_production_watchdog_bounds_never_closed_stdin`; RM18 |
| p6-F3 | MEDIUM | fixed (`006451d`) — state files checked for the canaries | `test_payload_value_canaries` |
| p6-F4 | MEDIUM | fixed (`006451d`) — submission multiplicity and final offset asserted | `test_concurrent_hooks_state_safe` |
| p7-F1 | MEDIUM | fixed (`006451d`) — evaluator event with a real task | `test_jobs_exclude_evaluator`; RM15 |
| p7-F2 | MEDIUM | fixed (`006451d`) — fixture pinned at 722 bytes | `test_worst_case_plugin_tags_accepted` |
| p7-F3 | LOW | fixed (`006451d`) — bounded reads asserted on an instrumented stream | `test_oversized_line_never_held_whole` |
| p8-F1 | MEDIUM | fixed (`006451d`) — valid v11 source; refusal observed before any statement (strengthened here after RM16 first survived) | `test_rollback_step_guard_rechecks_version_under_lock`; RM16 |
| p8-F2 | MEDIUM | fixed (`006451d`) — appended task is on an unread page; its snapshot total asserted | `test_pagination_under_concurrent_ingest`; RM17 |
| p8-F3 | MEDIUM | fixed (`006451d`) — `provider` path canary across datasets | `test_export_value_canaries` |
| p9-F1 | MEDIUM | fixed (`006451d`) — launcher teardown removes only its own paths | `test_launcher_env_contract.py` |
| p9-F2 | MEDIUM | fixed (`006451d`) — a present launcher whose patch does not apply fails unless already patched | `test_launcher_env_contract.py::_launcher` |
| p9-F3 | MEDIUM | fixed (`006451d`) — distinguishable initial / older / latest responses | `test_jobs_latest_request_wins`; RM19 |
| p10-F1 | HIGH | fixed (plugin `cb56106`) — `work_type` closed list; `parent_project_name` part = known limitation (hostname-shape residual) | plugin `job.py`; `test_unknown_shape_valid_work_type_never_emitted`; RM14 |
| p10-F2 | MEDIUM | known limitation — launcher copies do not `unset` inherited job variables (shared files S1–S3, not changed) | Known Limitations |

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
6. **Coordinated shared files** S1–S4 (three `hermes.sh` copies, `/hermes` skill) applied through SESSION-COORDINATION, if not already done. **6a (user decision 2026-09-28):** apply the S2/S3 patches (`shared-patches/S2-…`, `S3-…`) from each repo root with `git apply` after the user approves; write the ledger row first and leave a message for the owner sessions; re-run `test_launcher_env_contract` against the real files afterwards.
7. **Plugin install + `hermes gateway restart`** only when no agent or JEV run is active: no `hermes -z` process, `evaluator_runs` has no `queued`/`running` row, the "hermes sırası" table shows no running job. Update the plugin source and the installed copy to the pushed commit, then `hermes gateway restart` from an external shell. Record PIDs before/after.
8. **Hook installer (S5)** on Windows and on hermes: `python producers/claude_code/install.py --dry-run` → show its content-free operation summary (never a settings diff) → announce in SESSION-COORDINATION → `--apply` (local backup written, never synced; atomic write aborts if another session changed the file). Windows: the producer URL comes from env or the local config file (the tailnet HTTPS name allowed by the `remote-access.conf` drop-in), never from docs. hermes: loopback default. Confirm PossibleSkills' hooks are unchanged in the resulting file. **Global hooks take effect in all running Claude Code sessions immediately, without a restart** (measured by PossibleSkills): the moment `--apply` finishes, every open session on that machine starts sending, so apply only after step 4 (backend with the Phase 2 code live), and uninstall first if anything misbehaves.
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
- code-review r1 p1-F1/p2-F1: the export's model rule and error-class rule are shape rules, so a path-like or host-like model string reported by a provider, or an identifier-shaped exception class name, is exported as is — accepted: values come from provider responses and normalized tags; the contract's residual paragraph states it.
- code-review r1 p2-F3: the dashboard export has no per-page 30 s timeout — accepted: single user; starting a new export supersedes and aborts a stalled run.
- code-review r1 p3-F1: two hooks that see the same stale lock at the same instant can both take it over — accepted: the checkpoint never moves backwards for the same file identity and the backend deduplicates by `client_event_id`, so the worst case is a duplicate POST, never a double count.
- code-review r1 p3-F2: a backlog left by a failed delivery is sent with the context (job) of the next hook on that transcript, so a transcript resumed under another job before the next successful hook tags the old calls with the new job — accepted: needs a failed delivery plus a resume under another job; no backlog boundary exists without a state-schema change.
- code-review r1 p4-F3: when a window ends right after a group's final-usage line and a later line of the same message carries another tool id, `tool_call_count` is low by that tool — accepted: tokens and cost are correct; not observed in the C0 corpus (18 640 groups); changing the C0 finality rule is out of scope.
- code-review r1 p10-F1 (part): `parent_project_name` is a normalized project name, so a project folder named like a host is sent as is — accepted: same hostname-shape residual as `project_name` (export contract).
- code-review r1 p10-F2: the launcher copies (S1–S3) export the validated job variables but do not `unset` inherited `TOKEN_INSPECTOR_JOB_*` values — accepted: the runner starts from a fresh non-interactive ssh shell where these are not set; changing the shared launcher copies needs a separate user decision.

## Open Items
| # | Item | Owner | Blocks |
|---|---|---|---|
| O1 | Resolved (user goal, 2026-09-28): deploy **once**, after the Claude integration review (as O10), with user approval. Live checks AC1.2, AC2.2, AC2.3 are recorded as "pending deploy" until then | — | Live AC checks |
| O2 | J0 / AC1.1 gate: does `hermes -z` load plugins in its own process? **Stop-and-ask** if no, if the evidence is missing or inconclusive, or if a finding contradicts an AC (default proposal for "no": launcher-posted job→session mapping record) — never automatic drift | Claude (read-only), then user | Phase 1 J1+ |
| O3 | C0 / AC2.1 gate: CC hook stdin fields, subagent layout, parent link and child id, streaming duplicates and finality rule, resumed copies, `<synthetic>` lines, error records, line sizes, in-place rewrites — rules into the Drift Log. **Stop-and-ask** when a fact lacks admissible evidence (backend.md C0) or contradicts an AC | Claude (read-only), then user | Phase 2 C1+ |
| O4 | Resolved: J2 measured 17 keys / 722 B → cap 1024 B, keys ≤ 20 (Drift Log) | — | — |
| O5 | Resolved (user, 2026-09-28): TI owns `AIFromScratch\scripts\hermes.sh` and `~/.claude/commands/hermes.md`; ledger rows added. TI edits them with a ledger line and a message to PossibleSkills | — | — |
| O6 | User decision 2026-09-28: S2/S3 — on deploy day TI applies the patches in `shared-patches/` with the user's approval (ledger row first; message left for the owner sessions). S1/S4 done in Phase 1. S5 at deploy (merge-only, PossibleSkills' lock file, their entries checked afterwards) | TI (user approval at deploy) | Phase 1 live for PEGA jobs, Phase 2 deploy |
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
| 2026-09-28 | **J0 / AC1.1 gate** — does `hermes -z` load plugins in its own process? Read-only source trace on hermes, installed Hermes Agent `~/.hermes/hermes-agent` @ `5646fed97eac67c5ec5b21e5c491309d8c97639d` (3 unrelated local modifications: `hermes_cli/cron.py`, `hermes_cli/subcommands/cron.py`, `package-lock.json`). No job run, nothing installed or changed, no content copied | **Verdict: YES.** `~/.local/bin/hermes` execs the venv python on `hermes` (entry) → `hermes_cli/main.py` `main()`: `-z` parses with `args.command = None`, which is in `_AGENT_COMMANDS = {None, "chat", "acp", "rl"}`, so `_prepare_agent_startup(args)` calls `hermes_cli.plugins.discover_plugins()` in this process (skipped only with `HERMES_SAFE_MODE` / `--safe-mode`, which `hermes.sh` never sets) → `_run_and_exit_oneshot` → `hermes_cli/oneshot.py` `run_oneshot` builds `run_agent.AIAgent(... platform="cli")` locally ("Bypasses cli.py entirely"; no gateway hand-off) → `agent/conversation_loop.py` calls `hermes_cli.plugins.has_hook/invoke_hook("pre_api_request" / "post_api_request" / "on_session_start")` on the process-global `get_plugin_manager()`; subagents (`tools/delegate_tool.py`, `subagent_start`) run in a `ThreadPoolExecutor` of the same process. Plugin enablement comes from `~/.hermes/config.yaml` `plugins.enabled: [token_inspector]`, read by that process. So env vars exported by the runner before `$AGENT -z` are in `os.environ` of the process whose plugin builds the events → proceed with J1 |
| — | AC1.2 live (AIFromScratch `hermes.sh` review job) | pending deploy |
| 2026-09-28 | AC1.2 launcher env contract, synthetic (`test_launcher_env_contract`: S1 real file; S2/S3 = owners' files with the O9 patch applied to a temp copy) | **14 passed**; live runs pending deploy (S2/S3 also pending the owners applying the patches) |
| 2026-09-28 | **C0 / AC2.1 gate** — read-only. (1) Hooks doc (`code.claude.com/docs/en/hooks.md`, Stop / SubagentStop / SessionEnd input sections). (2) Scratch `claude -p` (Claude Code 2.1.284, Windows) in a temp dir with a temp `--settings` file whose Stop / SubagentStop / SessionEnd hooks logged only stdin key names and content-free transcript structure; one run with one subagent, then `--resume`; global settings untouched; temp dir, settings, log and the scratch transcript dir deleted afterwards. (3) Throwaway scan scripts over the 194 local transcripts (132 main, 62 subagent) printing only key names, line types, counts, byte sizes and id-equality relations; two size/identity/prefix-hash snapshots for item 9 (hashes kept in the session scratchpad only). No value or content copied | **PASS** — rules 1–9 in the Drift Log ("C0 rule N"). Key relations: subagent first `promptId` = parent Agent tool-result `promptId` 60/60; lines with non-null `stop_reason` carry final usage 33 895/33 895; group lines interrupted only by tool-result/meta user lines; resumed copies keep `message.id` + `requestId` 62/62; largest usage line 126 KB; no in-place rewrite |
| 2026-09-28 | Phase 2 suites — local Windows: backend `python -m pytest -q` **488 passed** (incl. 26 browser); `py_compile` (incl. `producers/claude_code/*.py`), `node --check`, `git diff --check` clean; plugin **199 passed, 2 skipped**. hermes temp worktrees of the pushed branch: backend **448 passed, 15 skipped** — the first run had 1 failure in the Phase 1 test `test_repair_backup_accepts_real_concurrent_writer` (its tight concurrent writer loop starves the repair's lock: `sqlite error; nothing was changed`; 1 of 5 standalone reruns failed; unrelated to Phase 2, the repair script is untouched); the full rerun was green; plugin **201 passed** on 3.12.3 and Hermes runtime 3.11.15. `test_attribution_vectors_identical` ran (not skipped) on both machines | green; flaky Phase 1 test reported for a decision |
| 2026-09-28 | Changed existing expectations (Phase 2 C10 pairing): `tests/test_job_correlation.py::test_reserved_keys_normalization` — `{producer: other-producer, runtime: hermes-agent}` → `{attribution_invalid: true}`; `{attribution_invalid: true, runtime: app}` → `{attribution_invalid: true}` (plus a valid `app`/`app-provider` vector) | rule change, not a relaxation |
| 2026-10-01 | Flaky repair test fix (`fdbb1e5`, RP 19): `test_repair_backup_accepts_real_concurrent_writer`'s writer pauses 1 ms after each commit; repair script and assertion unchanged. Standalone on hermes (temp worktree of `fdbb1e5`, venv Python 3.12.3, SQLite 3.45.1): **10/10 passed**. Mutation (backup verification `b <= g <= a` → `b == g == a`, i.e. refuse any concurrent growth): red **10/10** on hermes, 12/20 on Windows (the original tight loop: 14/20 on Windows; the window is timing-dependent there), failing assertion `tests/test_repair_task_parents.py:258` (`repair.main(...) == 0` → 1); reverted, original string present | green; open item closed |
| 2026-10-01 | SQLite versions before `_m12` (database.md): Windows venv Python 3.13.1 / SQLite **3.45.3**; hermes venv Python 3.12.3 / SQLite **3.45.1**. Both ≥ 3.35 (rollback `DROP COLUMN`) and ≥ 3.24 (app minimum) | recorded |
| 2026-10-01 | Changed existing expectations (Phase 3 v12): `schema_version` 11 → 12 in `test_security_meta.py::test_meta_preflight_with_flags_off_and_valid_config` and `test_prompt_allowlist.py::test_allowlist_config_boundary`; `test_review_r1.py::test_schema_matches_independent_expectations` gains the new last `token_events` column `ingest_seq`; `test_migration_v11.py`: the fresh/re-upgraded version is `LATEST_SCHEMA_VERSION` (12), and the "no rollback step" case uses an unknown version 13 (12 now has a step) | schema bump, not a relaxation |
| 2026-10-01 | Phase 3 suites — local Windows (`c4a07bc`): backend `python -m pytest -q` **535 passed** (incl. 41 browser); `py_compile` (`*.py routes/*.py scripts/*.py producers/claude_code/*.py`), `node --check static/app.js`, `git diff --check` clean. One intermediate full run failed `test_jobs_api.py::test_job_cost_semantics`: `test_stable_ids_and_updated_at` had added a pricing rule for the shared model name `zz-unpriced-model`, and pricing rules survive the per-test cleanup; the export tests now use their own model names (`c4a07bc`), rerun green. hermes temp worktree `/tmp/o9-p3/` of `c4a07bc` (venv Python 3.12.3): backend **478 passed, 17 skipped**; repair test **10/10** standalone; worktree removed, prod checkout on `main` untouched. Plugin repo unchanged (no plugin suite needed) | green |
| 2026-10-01 | Phase 3 AC → test map (all passing): AC3.1 `test_export_endpoints_read_only_no_auth`, browser `test_csv_header_equals_fields`, `test_csv_jobs_and_tasks_headers`, `test_csv_empty_range_header_only`, `test_json_link_follows_inputs`; AC3.2 `test_export_field_allowlist_contract`, `test_export_tag_allowlist`, `test_export_not_reusing_events_listing`, `test_export_value_canaries`; AC3.3 `test_range_bounds_inclusive_exclusive`, `test_cohort_period_semantics`, `test_export_errors_static_no_echo`, browser `test_export_range_refused_without_request`, `test_export_error_mapped`, `test_export_error_policy_closed`; AC3.4 `test_pages_concatenate_to_full_result`, `test_pagination_under_concurrent_ingest`, `test_snapshot_job_membership_is_frozen`, `test_cursor_expires_on_mutation`, `test_cursor_ignores_rows_after_delete_and_reingest`, `test_v10_v12_old_code_round_trip`, `test_ingest_seq_monotonic_in_commit_order`, `test_ingest_seq_not_reused_after_delete`, `test_m12_fresh_equals_migrated`, `test_m12_backfill_order`, `test_null_seq_self_heal_on_startup`, `test_heal_is_atomic`, `test_rollback_runner_guards`, `test_rollback_to_v11_and_reupgrade`, `test_revision_bumps`, browser `test_export_loop_terminates`, `test_export_superseded_run_stops`, `test_export_run_parameters_frozen`; AC3.5 `test_envelope_staleness_coverage`, `test_legacy_events_inferred_runtime`; AC3.6 `test_zero_unknown_absent_distinct`, `test_event_job_ref_is_canonical`, `test_task_multi_value_fields`, `test_export_excludes_evaluator`, `test_invalid_pair_not_counted_end_to_end` (export part); AC3.7 `test_stable_ids_and_updated_at`; AC3.8 `test_export_contract_documented`, `test_export_busy_returns_503`; AC3.9 browser `test_csv_formula_injection_guard` | mapped |
| 2026-10-01 | Changed existing test (Phase 3): `test_dedup_authority.py::test_invalid_pair_not_counted_end_to_end` gains export assertions (the marked event is in no export dataset; legacy is inferred); nothing relaxed | extension |
| — | AC2.2 live (Windows Claude Code turn) | pending deploy |
| — | AC2.3 live (devir run, `devir-baslat`) | pending deploy |

Mutation checks (one mutation at a time: break the control → named test red → revert; a survivor is a missing test unless marked redundant):

| # | Phase | Control | Result | Failing test(s) |
|---|---|---|---|---|
| PJ1 | 1 | `job.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_job_env_invalid_job_ref_dropped`, `test_plugin_payload_sentinel` |
| PJ2 | 1 | `job.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_job_env_invalid_work_type_dropped` |
| PJ3 | 1 | `job.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_llm_event_carries_job_tags` |
| PJ4 | 1 | `mapping.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_llm_event_carries_job_tags` |
| PJ5 | 1 | `job.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_job_env_read_once_per_process` |
| PJ6 | 1 | `__init__.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_child_event_carries_parent_project_name` |
| PJ7 | 1 | `__init__.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_invalid_parent_project_not_sent` |
| PJ8 | 1 | `job.py` — plugin job/parent tags control | caught (red → reverted → green) | `test_job_env_invalid_job_ref_dropped` |
| BJ1 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_normalization` |
| BJ2 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_normalization` |
| BJ3 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_normalization` |
| BJ4 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_normalization` |
| BJ5 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_never_reject` |
| BJ6 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_worst_case_plugin_tags_accepted` |
| BJ7 | 1 | `task_store.py` — backend control | caught (red → reverted → green) | `test_task_gets_first_job`, `test_task_belongs_to_one_job` |
| BJ8 | 1 | `task_store.py` — backend control | caught (red → reverted → green) | `test_task_gets_first_job` |
| BJ9 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_job_cost_semantics` |
| BJ10 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_job_cost_semantics` |
| BJ11 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_jobs_exclude_evaluator` |
| BJ12 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_task_belongs_to_one_job` |
| BJ13 | 1 | `task_store.py` — backend control | caught (red → reverted → green) | `test_cross_project_child_parent_ref` |
| BJ14 | 1 | `task_store.py` — backend control | caught (red → reverted → green) | `test_cross_project_child_either_order` |
| BJ15 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_invalid_parent_project_name_keeps_today_behaviour` |
| BJ16 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_dry_run_by_default` |
| BJ17 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_backs_up_before_apply` |
| BJ18 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_apply_relinks_and_reroots` |
| BJ19 | 1 | `routes/events.py` — backend control | caught (red → reverted → green) | `test_reserved_keys_normalization`, `test_jobs_exclude_invalid_attribution` |
| BJ20 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_conflicts_events_vs_tasks` |
| BJ21 | 1 | `routes/jobs.py` — backend control | caught (red → reverted → green) | `test_jobs_filters_select_whole_jobs` |
| BJ22 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_revalidates_under_write_lock` |
| BJ23 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_graph_cases` |
| BJ24 | 1 | `scripts/repair_task_parents.py` — backend control | caught (red → reverted → green) | `test_repair_backup_verified_during_ingest` |
| BJ25 | 1 | `migrations.py` — backend control | caught (red → reverted → green) | `test_cc_client_event_unique_across_projects` |
| FM1 | 1 | `static/app.js` — Jobs view control | caught (red → reverted → green) | `test_jobs_unpriced_never_zero` |
| FM4 | 1 | `static/app.js` — Jobs view control | caught (red → reverted → green) | `test_jobs_latest_request_wins` |
| FM8 | 1 | `static/app.js` — Jobs view control | caught (red → reverted → green) | `test_jobs_conflict_counts` |
| BJ26 | 1 | `hermes.sh` S1 (temp copy via `O9_LAUNCHER_ROOT`): work-type check removed | caught (2 S1 cases red; original 14/14 green) | `test_launcher_drops_invalid_work_type_and_attempt` |
| CM1 | 2 | `cc_events` — client_event_id from message.id + requestId | caught (red → reverted → green) | `test_client_event_id_from_message_and_request`, `test_rerun_and_resume_count_once` (tests/test_cc_events.py:66 AssertionError, tests/test_dedup_authority.py:161 assert) |
| CM2 | 2 | `cc_transcript` — kept line = last line of the group | caught (red → reverted → green) | `test_streaming_lines_counted_once_with_final_usage` (tests/test_cc_transcript.py:27 assert) |
| CM3 | 2 | `cc_transcript` — `<synthetic>` lines skipped | caught (red → reverted → green) | `test_synthetic_model_lines_skipped` (tests/test_cc_transcript.py:49 AssertionError) |
| CM4 | 2 | `cc_events` — prompt_tokens exclude cache | caught (red → reverted → green) | `test_token_mapping` (tests/test_cc_events.py:34 assert) |
| CM5 | 2 | `cc_events` — final ALLOWED_FIELDS filter | caught (red → reverted → green) | `test_events_built_only_from_allowlist`, `test_payload_sentinel_bytes` (tests/test_cc_events.py:74 AssertionError, tests/test_cc_hook.py:152 AssertionError) |
| CM6 | 2 | `cc_hook` — cursor advances only after a valid ack | caught (red → reverted → green) | `test_cursor_advances_only_on_valid_ack` (tests/test_cc_hook.py:287 AssertionError) |
| CM7 | 2 | `cc_client.valid_ack` — ack validation | caught (red → reverted → green) | `test_ack_validation`, `test_cursor_advances_only_on_valid_ack` (tests/test_cc_hook.py:288 KeyError, tests/test_cc_state_client.py:185 AssertionError) |
| CM8 | 2 | `cc_hook` — fail-open (catch BaseException, exit 0) | caught (red → reverted → green) | `test_hook_exits_zero_on_every_failure` (tests/test_cc_hook.py:214 AssertionError) |
| CM9 | 2 | `cc_hook` watchdog + `cc_client` 2 s timeout | caught (red → reverted → green) | `test_hook_bounded_on_hanging_backend` (the hook subprocess exceeded the bound: `subprocess.TimeoutExpired` at the bounded `run_hook_subprocess` call) |
| CM10 | 2 | `cc_hook` — silent output | caught (red → reverted → green) | `test_hook_silent_on_success` (tests/test_cc_hook.py:228 AssertionError) |
| CM11 | 2 | `cc_state` — state holds no path | caught (red → reverted → green) | `test_state_holds_only_offsets_and_ids` (tests/test_cc_state_client.py:76 assert) |
| CM12 | 2 | `cc_state` — at most 512 state files | caught (red → reverted → green) | `test_state_holds_only_offsets_and_ids` (tests/test_cc_state_client.py:83 AssertionError) |
| CM13 | 2 | `cc_client` — token never logged | caught (red → reverted → green) | `test_token_optional_and_never_logged` (tests/test_cc_state_client.py:224 AssertionError) |
| CM14 | 2 | `cc_events` — child parent link | caught (red → reverted → green) | `test_subagent_is_child_task` (tests/test_dedup_authority.py:190 AssertionError) |
| CM15 | 2 | `cc_attribution` — origin slug before git root | caught (red → reverted → green) | `test_attribution_order`, `test_devir_run_tags` (tests/test_cc_attribution.py:32 AssertionError, tests/test_cc_hook.py:125 AssertionError) |
| CM16 | 2 | `install.py` — merge, never replace `hooks` | caught (red → reverted → green) | `test_installer_preserves_foreign_hooks` (tests/test_cc_install.py:90 KeyError) |
| CM17 | 2 | `install.py` — idempotent (existing-entry check) | caught (red → reverted → green) | `test_installer_idempotent_backup_uninstall` (tests/test_cc_install.py:112 assert) |
| CM18 | 2 | `install.py` — backup before apply | caught (red → reverted → green) | `test_installer_idempotent_backup_uninstall` (tests/test_cc_install.py:109 assert) |
| CM19 | 2 | `_normalize_reserved` — runtime↔producer pairing | caught (red → reverted → green) | `test_runtime_producer_pairing` (tests/test_dedup_authority.py:74 AssertionError) |
| CM20 | 2 | producer stdlib-only | caught (red → reverted → green) | `test_cc_producer_is_stdlib_only` (tests/test_cc_stdlib.py:34 AssertionError) |
| CM21 | 2 | `cc_transcript` — group finality at the window end | caught (red → reverted → green) | `test_streaming_group_split_across_reads` (tests/test_cc_transcript.py:69 AssertionError) |
| CM22 | 2 | `cc_hook` — per-batch checkpoint | caught (red → reverted → green) | `test_checkpoint_per_batch_progress` (tests/test_cc_hook.py:316 AssertionError) |
| CM23 | 2 | `cc_hook` — rejected items dropped, cursor advances | caught (red → reverted → green) | `test_rejected_items_dropped_and_counted` (tests/test_cc_hook.py:363 AssertionError) |
| CM24 | 2 | `cc_hook` — truncation / replacement reset | caught (red → reverted → green) | `test_truncated_or_replaced_transcript_resets` (tests/test_cc_hook.py:403 assert) |
| CM25 | 2 | `cc_transcript` — oversized line skipped by scanning | caught (red → reverted → green) | `test_oversized_lines` (tests/test_cc_transcript.py:176 AssertionError) |
| CM26 | 2 | `cc_hook` — backlog drain | caught (red → reverted → green) | `test_sessionend_backlog_is_drained` (tests/test_cc_hook.py:449 assert) |
| CM27 | 2 | `cc_state` — per-transcript lock | caught (red → reverted → green) | `test_concurrent_hooks_state_safe` (tests/test_cc_state_client.py:112 assert) |
| CM28 | 2 | `cc_state` — monotonic checkpoint | caught (red → reverted → green) | `test_concurrent_hooks_state_safe` (tests/test_cc_state_client.py:130 AssertionError) |
| CM29 | 2 | `cc_hook` — watchdog before stdin | caught (red → reverted → green) | `test_hook_bounded_on_blocked_stdin_and_slow_io` (case (a): the blocked-stdin hook never exited → `subprocess.TimeoutExpired` in `proc.wait`) |
| CM30 | 2 | `_insert_event` — cross-project `cc-` dedup | caught (red → reverted → green) | `test_cross_project_cc_dedup` (tests/test_dedup_authority.py:134 TypeError) |
| CM31 | 2 | `cc_events` — session id pseudonym | caught (red → reverted → green) | `test_payload_value_canaries` (tests/test_cc_hook.py:178 AssertionError) |
| CM32 | 2 | `cc_events` — model value rule | caught (red → reverted → green) | `test_payload_value_canaries` (tests/test_cc_hook.py:177 AssertionError) |
| CM33 | 2 | `_normalize_reserved` — invalid pair mark | caught (red → reverted → green) | `test_invalid_pair_not_counted_end_to_end`, `test_runtime_producer_pairing` (tests/test_dedup_authority.py:74 AssertionError, tests/test_dedup_authority.py:95 assert) |
| CM34 | 2 | `install.py` — content-free output | caught (red → reverted → green) | `test_installer_output_is_content_free` (tests/test_cc_install.py:73 AssertionError) |
| CM35 | 2 | `install.py` — exact-command ownership | caught (red → reverted → green) | `test_installer_exact_ownership` (tests/test_cc_install.py:143 assert) |
| CM36 | 2 | `install.py` — unchanged-source check | caught (red → reverted → green) | `test_installer_aborts_on_concurrent_change` (tests/test_cc_install.py:156 Failed) |
| CM37 | 2 | `cc_events` — child turn_id = run id | caught (red → reverted → green) | `test_child_turn_id_is_run_id` (tests/test_cc_events.py:147 assert) |
| XM1 | 3 | `routes/export.py` — event field allowlist (`error_message` added) | caught (red → reverted → green) | `test_export_field_allowlist_contract` (tests/test_export_contract.py:141 AssertionError) |
| XM2 | 3 | `routes/export.py` — tag allowlist (full `tags` added) | caught | `test_export_tag_allowlist` (tests/test_export_contract.py:155 AssertionError) |
| XM3 | 3 | `routes/export.py` — `to` exclusive (`<` → `<=`) | caught | `test_range_bounds_inclusive_exclusive` (tests/test_export_contract.py:204 AssertionError) |
| XM4 | 3 | `routes/export.py` — `invalid_range` body echoes `from` | caught | `test_export_errors_static_no_echo` (tests/test_export_contract.py:247 AssertionError) |
| XM5 | 3 | `routes/export.py` — snapshot bound `ingest_seq <= :as_of` dropped | caught | `test_pagination_under_concurrent_ingest` (tests/test_export_pagination.py:56 AssertionError) |
| XM6 | 3 | `routes/export.py` — strict keyset (`>` → `>=`) | caught | `test_pages_concatenate_to_full_result` (tests/test_export_pagination.py:37 AssertionError) |
| XM7 | 3 | `routes/export.py` — cursor accepted for another range | caught | `test_export_errors_static_no_echo` (tests/test_export_contract.py:263 AssertionError) |
| XM8 | 3 | `routes/export.py` — `cost_usd` = cost or 0 | caught | `test_zero_unknown_absent_distinct` (tests/test_export_contract.py:311 AssertionError) |
| XM9 | 3 | `routes/export.py` — `reasoning_tokens` emitted for CC events | caught | `test_zero_unknown_absent_distinct` (tests/test_export_contract.py:314 AssertionError) |
| XM10 | 3 | `routes/export.py` — evaluator predicate dropped | caught | `test_export_excludes_evaluator` (tests/test_export_contract.py:357 AssertionError) |
| XM11 | 3 | `routes/export.py` — `coverage.not_measured` = `[]` | caught | `test_envelope_staleness_coverage` (tests/test_export_contract.py:278 AssertionError) |
| XM12 | 3 | `routes/export.py` — legacy `runtime_inferred` false | caught | `test_legacy_events_inferred_runtime` (tests/test_export_contract.py:291 AssertionError) |
| XM13 | 3 | `routes/export.py` — router depends on `require_sensitive_auth` | caught | `test_export_endpoints_read_only_no_auth` (tests/test_export_contract.py:64 AssertionError) |
| XM14 | 3 | `routes/events.py` — `ingest_seq` NULL at insert | caught | `test_ingest_seq_monotonic_in_commit_order` (tests/test_migration_v12.py:214 AssertionError; first run hit a `TypeError` while sorting, so the test gained an explicit no-NULL assertion and the mutation was re-run) |
| XM15 | 3 | `database.py` — startup NULL backfill skipped | caught | `test_null_seq_self_heal_on_startup` (tests/test_migration_v12.py:111 AssertionError) |
| XM16 | 3 | `routes/export.py` — task job from live `tasks.job_ref` | caught | `test_snapshot_job_membership_is_frozen` (tests/test_export_pagination.py:86 AssertionError) |
| XM17 | 3 | `routes/events.py` — `COALESCE(MAX(ingest_seq), 0) + 1` | caught | `test_ingest_seq_not_reused_after_delete` (tests/test_migration_v12.py:237 AssertionError) |
| XM18 | 3 | `routes/export.py` — revision/epoch comparison skipped | caught | `test_cursor_expires_on_mutation` (tests/test_export_pagination.py:114 AssertionError) |
| XM19 | 3 | `routes/settings.py` — recost revision bump removed | caught | `test_cursor_expires_on_mutation` (tests/test_export_pagination.py:114 AssertionError; run with `-x`, so `test_revision_bumps` was not reached) |
| XM20 | 3 | `routes/export.py` — `session_id` / `model` / `error_type` raw | caught | `test_export_value_canaries` (tests/test_export_contract.py:182 AssertionError) |
| XM21 | 3 | `routes/export.py` — task/job sums clipped to `[from, to)` | caught | `test_cohort_period_semantics` (tests/test_export_contract.py:222 AssertionError) |
| XM22 | 3 | `routes/export.py` — event `job_ref` from its own tag | caught | `test_event_job_ref_is_canonical` (tests/test_export_contract.py:326 AssertionError) |
| XM23 | 3 | `routes/export.py` — `attribution_invalid` predicate dropped (marked events inferred as `hermes-agent`) | caught | `test_invalid_pair_not_counted_end_to_end` (tests/test_dedup_authority.py:101 AssertionError) |
| XM24 | 3 | `migrations.py` — heal commits per row | caught | `test_heal_is_atomic` (tests/test_migration_v12.py:140 AssertionError) |
| XM25 | 3 | `scripts/rollback_schema.py` — in-transaction source-version check skipped | caught | `test_rollback_runner_guards` (tests/test_migration_v12.py:164 AssertionError) |
| FM2 | 3 | `static/app.js` `csvCell` — formula prefix removed | caught | `test_csv_formula_injection_guard` (tests/browser/test_export_download.py:213 AssertionError) |
| FM3 | 3 | `static/app.js` `toCsv` — header from `Object.keys(items[0])` | caught | `test_csv_header_equals_fields` (tests/browser/test_export_download.py:155 AssertionError) |
| FM5 | 3 | `static/app.js` `exportErrorMessage` — unknown code echoed | caught | `test_export_error_policy_closed[unknown code]` (tests/browser/test_export_download.py:135, the fixed-message wait in `_status` timed out: the page showed the echoed code) |
| FM6 | 3 | `static/app.js` `fetchAllExport` — run-id checks dropped | caught | `test_export_superseded_run_stops` (tests/browser/test_export_download.py:331 AssertionError: the superseded run made a second request) |
| FM7 | 3 | `static/app.js` `fetchAllExport` — seen-cursor check dropped | caught | `test_export_loop_terminates` (tests/browser/test_export_download.py:135, the fixed "Export failed." wait in `_status` timed out: the loop kept fetching) |

Phase 3 mutation runner: one mutation at a time, restored byte-for-byte in a `finally` block and compared after each mutation (CRLF-aware); `git status` showed no code file modified afterwards. **30/30 caught.**

### Code review r1 — fix mutations (RM*)

Runner: scratchpad `mutate_r1.py` (`__main__` guard, `encoding="utf-8", errors="replace"`, file restored byte-for-byte in `finally` and compared; originals checked after each run).

| # | Fix | Control | Result | Failing test(s) |
|---|---|---|---|---|
| RM1 | p1-F2 | `routes/jobs.py` — `JOB_REF_RE` re-check of the job row removed | caught | `test_jobs_legacy_tag_values_never_returned` (tests/test_jobs_api.py) |
| RM2 | p1-F2 | `routes/jobs.py` — runtime closed list removed | caught | `test_jobs_legacy_tag_values_never_returned` |
| RM3 | p1-F2 | `routes/jobs.py` — unknown work type kept raw instead of `other` | caught | `test_jobs_legacy_tag_values_never_returned` |
| RM4 | p1-F3 | `routes/export.py` — `limit` length cap removed | caught | `test_export_oversized_numbers_rejected` (tests/test_export_contract.py) |
| RM5 | p1-F3 | `routes/export.py` — cursor `s`/`r` int64 range check removed | caught | `test_export_oversized_numbers_rejected` |
| RM6 | p1-F3 | `routes/export.py` — cursor `k` int64 range check removed | caught | `test_export_oversized_numbers_rejected` |
| RM7 | p2-F2 | `scripts/repair_task_parents.py` — backup reserved without `O_EXCL` | caught | `test_repair_backup_never_overwritten_in_same_second` (tests/test_repair_task_parents.py) |
| RM8 | p3-F3 | `producers/claude_code/install.py` — backup opened with `wb` instead of `xb` | caught | `test_installer_backup_never_overwritten_in_same_second` (tests/test_cc_install.py) |
| RM9 | p3-F4 | `install.py` — shell-metacharacter guard removed | caught | `test_installer_rejects_shell_metacharacters_in_hook_path` (tests/test_cc_install.py) |
| RM10 | p3-F3 | `install.py` — partial backup not removed after a failed write | caught | `test_installer_removes_partial_backup_after_write_failure` |
| RM11 | p3-F5 | `cc_hook.py` — failing producer import re-raised instead of a silent exit 0 | caught | `test_hook_import_failure_is_silent_and_fail_open` (tests/test_cc_hook.py) |
| RM12 | p4-F1 | `cc_events.py` — any string kept as `finish_reason` | caught | `test_model_and_finish_reason_value_rules` (tests/test_cc_events.py) |
| RM13 | p4-F2 | `cc_attribution.py` — normalized host names not added | caught | `test_normalized_hostname_git_root_falls_through` (tests/test_cc_attribution.py) |
| RM14 | p10-F1 | plugin `job.py` — any non-empty work type kept | caught | plugin `test_job_env_invalid_work_type_dropped` (tests/test_job_tags.py) |
| RM15 | p7-F1 | `routes/jobs.py` — evaluator predicate removed from `_COUNTED` | caught | `test_jobs_exclude_evaluator` (tests/test_jobs_api.py) |
| RM16 | p8-F1 | `scripts/rollback_schema.py` — in-transaction source-version guard skipped | caught (survived the hermes version of the test; the test now records the columns at the guard read) | `test_rollback_step_guard_rechecks_version_under_lock` (tests/test_migration_v11.py) |
| RM17 | p8-F2 | `routes/export.py` — task snapshot filter `ingest_seq <= :as_of` removed | caught | `test_pagination_under_concurrent_ingest` (tests/test_export_pagination.py) |
| RM18 | p6-F2 | `cc_hook.py` — `HOOK_BOUND_S` 5 → 60 | caught | `test_production_watchdog_bounds_never_closed_stdin` |
| RM19 | p9-F3 | `static/app.js` — latest-request generation check removed | caught | `test_jobs_latest_request_wins` (tests/browser/test_jobs_view.py) |

Code review r1 fix mutations: **19/19 caught** (RM1–RM19), originals verified after every run; no code file left modified.

| Date | Check | Result |
|---|---|---|
| 2026-10-02 | Windows suites after the code-r1 fixes | backend **546 passed** (incl. browser; first run 545 + 1 flaky, see next row); plugin **213 passed, 2 skipped** |
| 2026-10-02 | Flake `test_truncated_or_replaced_transcript_resets` | 1 of 12 standalone runs failed: the bare `"a3"` check matched inside a random sha256 pseudonym; the check now looks for the quoted JSON string; 15/15 passed (CLAUDE.md RP 21) |
| 2026-10-02 | hermes code job (worktrees `/tmp/o9-code/`) | backend 491 passed, 15 skipped (`-m "not browser"`); plugin 215 passed — before the RM16 test strengthening and the flake fix |
