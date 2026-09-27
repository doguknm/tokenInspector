# Status — Task-Level Telemetry Pilot (Hermes plugin fixes, tasks/JEV schema, human-label comparison)

**Started**: 2026-09-27
**Last updated**: 2026-09-27 (plan r2 fixes applied — **plan LOCKED**; any later change goes to the Drift Log only)

## Execution Plan
_Chosen by the user on 2026-09-27 (new-plan Step 3c). Every step is executed by Claude directly — **no handoffs** (`handoffs/` does not exist)._

| Step | Executor | Model | Skill / plugin | Handoff |
|---|---|---|---|---|
| d0-discovery, database, backend (A + B), tests-other, maintenance-docs | Claude Code | Opus 5.5 | — | no |
| frontend | Claude Code | Opus 5.5 | `frontend-design` | no |
| tests-e2e | Claude Code | Opus 5.5 | browser tools | no |
| hermes plan review | Hermes (driven by Claude) | hermes default | — | done (2 rounds) |
| hermes code review | Hermes (driven by Claude) | hermes default | — | no — spec in `## Hermes Code Review` |
| review | Claude Code | Opus 5.5 | — | no |

## Lane Status
| Lane | Tool | Status | Notes |
|---|---|---|---|
| d0-discovery | Claude Code (read-only on hermes) | done 2026-09-27 — user acknowledged; decisions in Drift Log | **Blocking.** Produces `discovery-evidence.md` (see Open Items). The user acknowledges it before `_m10` backfill semantics and plugin 0a-role/0c are implemented |
| database | Claude Code (Opus 5.5, direct) | done 2026-09-27 (`42d447a`) | After D0. Integrity-verified online backup before migrating. Deploy plugin 0b (spool) before migration v10 |
| backend | Claude Code (Opus 5.5, direct) | in progress — Part B 0a/0b done (plugin `969401b`, `1fb00b3`) and **deployed 2026-09-27** (user approval; gateway restarted); Part A next | Part A = this repo. Part B = Hermes plugin, implemented and tested on hermes only. Order: 0a/0b → migration → Part A → 0c |
| frontend | Claude Code (Opus 5.5, direct, `frontend-design` skill) | not started | Tasks view in `static/` (vanilla JS + Chart.js, no build step) |
| tests-other | Claude Code (Opus 5.5, direct) | not started | Can run in parallel with frontend once database/backend lanes are complete. Plugin tests run on hermes. Includes the Playwright stub suite (`tests/browser/`) |
| tests-e2e | Claude Code (Opus 5.5, direct, browser tools) | not started | Run after frontend lane is complete. The dev server runs against the demo DBs from `scripts/seed_tasks_demo.py` (`standard`, `many-scored`). Playwright suites stay in tests-other |
| maintenance-docs | Claude Code (backend lane implementer) | not started | Owns README.md, CHANGELOG.md, ARCHITECTURE.md, AGENTS.md, CONTRIBUTING.md, `docs/adr/002-task-prompt-retention-and-jev.md`, project CLAUDE.md (Key File Locations / Recurring Problems) and `.github/workflows/ci.yml` (scripts compile + Playwright job). Must be done before hermes-review; checked by the review lane |
| hermes-review | Hermes (driven by Claude Code) | not started | After tests-e2e and maintenance-docs, before review — spec: `## Hermes Code Review` below |
| review | Claude Code (Opus 5.5, direct) | not started | Run last — full integration review |
| activation-gate | User | not started | See "Activation Gate". No capture or JEV flag is enabled in prod before it passes |
| pilot (Faz 3) | User + Claude Code | not started | Prospective collection only. Starts when the gate has passed and ≥ 50 eligible completed root tasks exist (≥ 65 recommended). Order: select → blind label → score → report (paired cohort, ≥ 40 pairs) |

## Activation Gate
_None of `STORE_TASK_PROMPTS`, plugin `capture_task_prompt` or `JEV_ENABLED` may be enabled in production until phase (a) is complete. The gate has three phases in order (r2 item 8). Only the user may check the approval items._

**(a) Approval + configuration preflight — all flags still OFF**
- [ ] ADR-002 "Approval" section filled in and dated by the user, covering:
  - [ ] data categories sent/stored: redacted task prompt text (≤ 32,000 chars), request-composition counts, tool names, optional scrubbed label notes;
  - [ ] **both** JEV provider routes: typesafe-ai (primary) and digitalocean (fallback on 429);
  - [ ] residual redaction risk: `task-redact-v1` is pattern-based and not exhaustive;
  - [ ] provider retention: `no_training: all` / `zdr: none` are descriptive gateway metadata, not a verified guarantee. The local 30-day purge does not reach provider copies.
- [ ] `INGEST_TOKEN` (≥ 16 chars), `TASK_PROMPT_PURGE_INTERVAL_S` (1..21600) and the budget env values are set in the service unit. `INGEST_TOKEN` is also set in the plugin config.
- [ ] Service restarted with the flags OFF. `/api/meta` shows `config_errors == []`. `task_prompt_capture_disabled_reason = 'not_enabled'` and `jev_disabled_reason = 'not_enabled'` are **accepted** in this phase.
- [ ] Plugin 0b (spool/dead-letter/scrub/capture-time expiry) deployed and verified on hermes (AC2/AC2e tests green).
- [ ] `/api/tasks/retention-status` shows `last_purge_ok=true`.

**(b) Controlled activation — one step at a time, verifying (c) after each step**
1. [ ] Set `STORE_TASK_PROMPTS=1` and restart the service.
2. [ ] Set plugin `capture_task_prompt=true` and run `hermes gateway restart` from an external shell.
3. [ ] Later, and separately, set `JEV_ENABLED=1` (plus `AI_GATEWAY_API_KEY` via `EnvironmentFile`) and restart.

**(c) Post-start verification — required after each (b) step. If any check fails, turn that flag off again and log it in the Drift Log**
- [ ] After step 1: `/api/meta` shows `task_prompt_capture=true` with **no** disabled reason and `config_errors == []`. retention-status shows `scheduled=true` and `last_purge_ok=true`.
- [ ] After step 2: a test prompt containing a fake secret canary (e.g. `sk-ant-CANARY…`) is stored scrubbed. Check with `GET /api/tasks/{ref}?include_prompt=true` and the token. The spool directory holds no unscrubbed canary.
- [ ] After step 3: `/api/tasks/evaluator-status` shows `enabled=true`, `disabled_reason=null`, no `invalid_budget_config`, and empty `provider_cooldowns`.

## Hermes Code Review
_Spec run by Claude directly after the implementation lanes (not a handoff)._

**Hermes code review** of **Task-Level Telemetry Pilot** — run after the implementation lanes.
Hermes reviews; Claude drives it, triages every finding with the user and applies only the
approved fixes. Hermes writes no code and changes no file.

- **Content to review.**
  - Backend: `git diff 0aab993 -- . ':!Plans'` in the tokenInspector repo (PLAN_BASE = `0aab993`), plus the full text of new untracked files (`git ls-files --others --exclude-standard`, excluding `Plans/`).
  - Plugin (Part B): `git diff df5eb43` in `~/Projects/token_inspector` on hermes (PLUGIN_BASE = `df5eb43`, recorded 2026-09-27) plus its new untracked files.
  - Read at run time so drift is included: `2026-09-27-summary.md`, the **Acceptance Criteria** section of every lane file, and this file's Drift Log and Known Limitations (so Hermes does not re-raise accepted items).
- **Review focus.** Correctness bugs with a concrete failing scenario; every AC implemented and covered by a test; security (injection, authz, secrets in logs or model-visible arguments); privacy/retention (prompt capture, redaction, purge); error paths; contract mismatches between lanes; behaviour that deviates from the lane specs without a Drift Log entry.
- **Protocol.** The "Hermes review protocol" section of `C:\Users\Bentego_Admin\.claude\commands\new-plan.md`, with TAG = `task-telemetry-jev-pilot-code-r<N>` and deliverable = code. Script: `C:/Users/Bentego_Admin/Projects/AIFromScratch/scripts/hermes.sh` (the project has no `scripts/hermes.sh`). Before sending, follow the hermes single-job queue rule ("hermes sırası" table in `C:\Users\Bentego_Admin\.claude\SESSION-COORDINATION.md` + message the other session; mark done and notify afterwards). Max 2 rounds; no round 3.
- **When done.** Set the `hermes-review` row to done, then run the Claude integration review.

## Drift Log
_Append here when any lane discovers the spec is wrong. Do not edit lane files mid-flight._
- 2026-09-27 (planning): Brief AC4 as first written implied historical tasks could feed the pilot. Prod has 0 stored prompts, so the backfill is metadata-only and the pilot is prospective. The brief was corrected in r1 (item 20).
- 2026-09-27 (planning): Plugin 0c (prompt, hierarchy, composition, session-end tag) added beyond the brief's Faz 0. It is now gated on `GET /api/meta` (r1 item 17).
- 2026-09-27 (planning): `backup_database_if_needed` moves to the SQLite online-backup API plus a pre-migration integrity check (r1 item 22).
- 2026-09-27 (planning, r2): Migrations run on a dedicated migration engine with explicit `BEGIN IMMEDIATE`. pysqlite does not open a transaction before DDL; this addresses the r2-A UNSURE, and AC3f tests it. **Plan locked after this entry.**
- 2026-09-27 (D0, **user decision**): **Task unit = Hermes turn, not `task_id`.** Evidence (discovery-evidence.md): the gateway passes `task_id = session_id` and the API server `session_id or uuid`, so 73/244 task_ids span 2–13 user turns (median gap 31 min). Hermes mints `turn_id = "{session}:{task}:{hex8}"` once per `run_conversation()` = one user prompt → final response; 300 turns, none spans two task_ids, every llm_request has one. Applies to every lane: the task key everywhere (backfill `_m10`, live derivation, `task_ref`, uniqueness, completion, sampling, tests) is `(project_name, session_id, turn_id)` instead of `(project_name, task_id)`; `task_ref = sha256(project + "" + session_id + "" + turn_id)[:32]` (same 0x1f separator as the original spec; a missing session_id is stored as `''`); the Hermes `task_id` is kept on `tasks` as `source_task_id` (grouping only). "Next task start in the same session" (completion) = next turn. Historical: 300 metadata-only tasks.
- 2026-09-27 (D0, **user decision**): **`role`** comes from `subagent_start.child_role` for child sessions; every other turn is `primary`. No API hook carries a role. AC1's role part is satisfied by this rule (plugin sets `role` on events of a session registered as a child, else `primary`).
- 2026-09-27 (D0, **user decision**): **Hierarchy** from Hermes `subagent_start(parent_session_id, parent_turn_id, child_session_id, child_role, …)` and `subagent_stop`: plugin 0c registers both hooks; a child session's turns get `parent_task_ref = task_ref(project, parent_session_id, parent_turn_id)` and `root_task_ref` from the parent chain; `hierarchy_status='known'`. Turns of sessions never seen in `subagent_start` are roots (`hierarchy_status='root'`). Historical data has no link → `hierarchy_status='unknown'` (excluded from root-only sampling, which is prospective anyway).
- 2026-09-27 (D0): Session phase is stored in `token_events.finish_reason` (`start`/`end`/`shutdown`/`new_session`), not `tags.session_phase`. Session-end markers = `event_type='session' AND finish_reason IN ('end')`, plus `on_session_finalize` events; all 72 historical `end` events carry `task_id`/session_id.
- 2026-09-27 (D0): Complexity provenance is explicit: all 8,484 historical complexity rows carry `tags.complexity_version='request-shape-v1'` (+ `complexity_source='deterministic_metadata'`). `start_complexity_candidate` reads the method from `complexity_method` → `tags.complexity_method` → **`tags.complexity_version`**. The `-inferred` method and `BACKFILL_INFER_RS1` are not needed and are dropped.
- 2026-09-27 (D0): Request composition is captured at `pre_api_request` (kwargs `request_messages`, `conversation_history`, `message_count`, `approx_input_tokens`, `request_char_count`), not `pre_llm_call`. File-reading tools: `read_file`, `search_files`, `read_terminal`. Plugin in-memory queue bound: 2,048 events.
- 2026-09-27 (database lane, follows the task-unit decision): `tasks` columns become `session_id TEXT NOT NULL` (`''` when absent), `turn_id TEXT NOT NULL`, `source_task_id TEXT` (Hermes task_id), `parent_task_ref`/`root_task_ref` (task refs, no FK) instead of `task_id`/`parent_task_id`/`root_task_id`; unique key `ux_tasks_project_session_turn(project_name, session_id, turn_id)`; `ix_tasks_project_parent` → `ix_tasks_parent(parent_task_ref)`. The `token_events` lookup index is `ix_token_events_project_session_turn(project_name, session_id, turn_id)` instead of `(project_name, task_id)`. A valid turn is a non-empty `turn_id` not in (`unknown`, `session`) on a non-`session` event.
- 2026-09-27 (database lane): session-end markers = `event_type='session' AND finish_reason IN ('end','shutdown','session_boundary','new_session')` (plugin phases for `on_session_end`, `on_session_finalize` and `on_session_reset`); `start` is not a marker.
- 2026-09-27 (database lane): the verified backup opens the `.bak-v*` file in WAL mode, so `-wal`/`-shm` siblings can appear next to it. Backend §5 backup re-purge must glob `<DB_PATH>.bak-v*` **excluding** `-wal`/`-shm`/`-journal` files (they are not databases) and checkpoint each backup before VACUUM. AC3c compares `sqlite_master.sql` for the six new tables; for `token_events` it compares `table_info` + `index_list`, because SQLite appends ALTER-added columns to the stored CREATE text differently from `create_all`.
- 2026-09-27 (backend Part A): `EventIn` carries the parent as `parent_session_id` + `parent_turn_id` (from `subagent_start`) instead of `parent_task_id`/`root_task_id`; the backend computes `parent_task_ref` and takes `root_task_ref` from the parent row (or the parent itself when it is not stored yet). `TaskItem` exposes `turn_id`, `source_task_id`, `parent_task_ref`, `root_task_ref`; `children[]` are tasks whose `parent_task_ref` is this task.
- 2026-09-27 (tests): `tests/conftest.py` disposes the async engine before every test, because each test runs in its own event loop and a pooled-connection queue bound to an earlier loop fails once concurrency exceeds the pool size (surfaced by the 20-way concurrent ingest test).
- 2026-09-27 (backend §6): a task the worker skips at run time (`not_found`, `evaluator_task`, `no_prompt`, `prompt_expired`, `prompt_purged`, `already_scored`) gets a JEV `task_evaluations` row with `status='skipped'` and the reason in `error_type`, so `evaluator-runs/{id}` can count `skipped` without a schema change. A task stopped by the budget before its first call gets no row. `/evaluate` and `evaluator-status` use the worker's clock (`jev_scorer.now`) for cooldowns and spend.
- 2026-09-27 (backend §5): the retention loop sleeps one interval before its first purge, because the startup purge already ran before serving.

## Hermes Reviews
_Max 2 rounds per deliverable (plan, code). No round 3. Reports: Plans/task-telemetry-jev-pilot/hermes/._

| Deliverable | Round | Date | Findings | Fixed | Known limitation | Rejected |
|---|---|---|---|---|---|---|
| plan | r1 | 2026-09-27 | plan r1 — 2 parts (A 20 + B 16 findings, 30 unique) — user decision: all 30 fixed (item 25 as label+test) — 2026-09-27 | 30 | 0 | 0 |
| plan | r2 | 2026-09-27 | plan r2 — 2 parts (A 7 + B 5 findings + 2 UNSURE contract gaps = 13 items) — user decision: all 13 fixed; verified by tests, no round 3 — 2026-09-27 | 13 | 0 | 0 |

Triage: `hermes/plan-r1-triage.md` and `hermes/plan-r2-triage.md`. Raw findings: `hermes/task-telemetry-jev-pilot-plan-r1-A.md`, `-B.md` and `hermes/task-telemetry-jev-pilot-plan-r2-A.md`, `-B.md`.

Each r2 fix has an owned test in tests-other.md, marked `(r2 item N)`. The plan deliverable has used its 2 rounds; the next Hermes round is the **code** review (`code-r1`).

## Known Limitations
_Review findings the user chose not to fix — one line each: `<plan|code>-review r<N> F<k>: <what> — <why accepted>`._

## Open Items
Each item has an owner, an evidence artifact and the work it blocks. "discovery-evidence.md" means `Plans/task-telemetry-jev-pilot/discovery-evidence.md`, which holds counts, field names and hook names only, never content.

| # | Item | Owner | Evidence artifact | Blocks |
|---|---|---|---|---|
| O1 | Is `task_id` = one user prompt → final stop? Which hook mints it? | Claude Code (hermes, read-only) | discovery-evidence.md: database.md D0 query output + hook evidence | `_m10` backfill, plugin 0c, the pilot |
| O2 | Hook fields: `role` source, delegation parent id, session end/finalize hook, request messages at `pre_llm_call`, file-read tool names, in-memory queue bound | Claude Code (hermes) | discovery-evidence.md + redacted payload fixtures in the plugin `tests/` | plugin 0a (role), 0c, AC9/AC10 |
| O3 | Historical complexity provenance (tag present? plugin sole writer?) | Claude Code (hermes, read-only) | discovery-evidence.md | Backfill method value (`request-shape-v1` vs `-inferred` vs NULL) |
| O4 | Tasks/day and pilot start date | Claude Code measures; user sets the date | discovery-evidence.md | Pilot scheduling |
| O5 | Plugin implementation route (SSH edit vs copy-and-sync), and a tarball backup of `~/Projects/token_inspector` | User decides; Claude Code executes | status.md note + backup path | All of Part B |
| O6 | Canonical backend workflow: hermes copy is canonical, GitHub `origin/main` is 4 commits behind. Where commits land and how they sync | User | status.md note | Backend implementation start |
| O7 | Service unit: `EnvironmentFile=%h/.config/token-inspector/secrets.env`, `INGEST_TOKEN`, `STORE_TASK_PROMPTS`, `JEV_ENABLED` | Claude Code prepares; user applies | README "Production" section | Activation Gate |
| O8 | ADR-002 Approval | User | ADR-002 | Activation Gate |

Resolved by D0 on 2026-09-27 (evidence: discovery-evidence.md, decisions: Drift Log): **O1** task unit = Hermes turn; **O2** role/hierarchy from `subagent_start`, session end via `finish_reason`, composition at `pre_api_request`, file tools `read_file`/`search_files`/`read_terminal`, queue bound 2,048; **O3** provenance = `tags.complexity_version`; **O4** measured ≈ 18 turns/day (last 7 days: 129) — the pilot start date is set by the user after the Activation Gate.

Resolved 2026-09-27 (user): **O6** backend is developed in the Windows repo on branch `feat/task-telemetry-jev-pilot`, tested there, pushed to the `hermes` remote (`hermes:Projects/tokenInspector`); GitHub is not touched; production deploy (pull + service restart) only with the user's approval. **O5** plugin backed up to `hermes:~/backups/token_inspector-df5eb43-20260927.tgz` (173 entries, clean tree @ `df5eb43`), cloned to `C:\Users\Bentego_Admin\Projects\token_inspector` (remote `hermes`, branch `feat/task-telemetry-jev-pilot`); tests run here and on hermes; install + gateway restart only with the user's approval.

Resolved in r1 (for reference):
- the JEV parser fixture now exists (`fixtures/jev-response-2026-09-27.json`; the 429 `Retry-After` presence was not recorded, so tests cover all header forms);
- backup-file retention (re-purge + VACUUM of DB-directory backups);
- budget defaults ($0.05/day, $0.50/month, 200 calls/day with durable reservations);
- redaction hardening in both plugin and backend;
- the blind label CLI;
- pilot thresholds with the minimum of 40 pairs and the `INCONCLUSIVE` outcome.

Carried out of scope from the brief: Faz 1, Faz 4, traceparent remapping, cross-source dedup, automated JEV at scale, provider-side erasure, backups outside the DB directory.

## Verification Log
| Date | Check | Result |
|---|---|---|
| 2026-09-27 | Baseline before implementation | backend 16 passed (local), plugin 25 passed (local) |
| 2026-09-27 | Database lane tests (`tests/test_migration_v10.py`) | 19 passed; full suite 35 passed |
| 2026-09-27 | Mutation checks on database controls | 5/5 caught: no `BEGIN IMMEDIATE` (2 fail), file copy instead of online backup (1), no backup verification (1), any complexity method accepted (4), reversed id tie-break (1) |
| 2026-09-27 | AC3b on hermes — online-backup clone of prod, `init_db()` on the clone | clone integrity ok; 9,289 rows before/after, same ids, **0 changed rows**, 0 non-null new columns; version 10; integrity ok; 299 tasks, 0 prompt text. Clone and its backups deleted; prod file only read |
| 2026-09-27 | AC3e — v9 app (`0aab993` worktree) on a migrated v10 DB (`scripts/check_v9_app_on_v10.py`) | POST 201, summary 200 |
| 2026-09-27 | Shared scrubber `task-redact-v1` (`redaction.py` = plugin `task_redact.py`, byte-identical; same `redaction_vectors.json`) | 26 vectors + idempotency pass in both repos |
| 2026-09-27 | Plugin 0a/0b tests (local) | 73 passed, 1 skipped (POSIX permissions) |
| 2026-09-27 | Plugin 0a/0b tests (hermes, temp clone of the branch, Python 3.13 venv) | 74 passed (permissions test included); hermes plugin working copy untouched (`main`, clean) |
| 2026-09-27 | Mutation checks on spool controls | 4/4 caught: no scrub before queue (1 fail), rejected replay stops the queue (1), no prompt expiry (3), auth treated as reject (2) |
| 2026-09-27 | Faz 0 deploy on hermes (user-approved) | source repo `main` and installed copy fast-forwarded to `1fb00b3`; `hermes gateway restart` OK (new PID); spool dir created `0700`. **Pending:** read-only AC1 check on prod rows written after the restart (needs real Hermes traffic) |
| 2026-09-27 | Backend Part A tests (security/meta 13, ingest derivation 18, tasks API 6, retention 14, JEV 26) | full backend suite 139 passed |
| 2026-09-27 | Mutation checks, backend Part A | ingest 5/5 (child downgrade, first-write-wins, old-capture discard, capture gate, start ordering); tasks API 2/2 (expired text served, include_prompt without auth); retention 3/4 single + VACUUM-only survived because `secure_delete` already zeroes freed pages, VACUUM+secure_delete together caught; JEV 6/6 (reservation commit, fallback after 5xx, Retry-After ignored, cooldown not persisted, budget check, legend check) |
