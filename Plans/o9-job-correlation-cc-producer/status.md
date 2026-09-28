# Status — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Started**: 2026-09-28
**Last updated**: 2026-09-28 (lane files written by the planner; plan not locked yet)
**Plan base commits**: backend PLAN_BASE = _TODO: written by the orchestrator when the plan is locked (backend HEAD at lock)_; plugin `4b069b4` (both on `feat/task-telemetry-jev-pilot`)
**Phases**: 1 = job correlation + cross-project child fix · 2 = Claude Code producer + dedup authority · 3 = versioned read-only export. Each phase ends at a checkpoint (commit + push in the repos it touched). Hermes code review and the Claude integration review run once, at the end.

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
| hermes code review | Hermes (driven by Claude) | hermes default (never named) | — | no — 1 round (brief); spec in `## Hermes Code Review` |
| review (Claude integration) | Claude Code (direct) | Opus 5.5 | — | no |

## Lane Status
_Tool / model / skill per step come from the execution plan chosen at /new-plan Step 3c. Claude steps run directly (no handoff); other tools use Plans/<slug>/handoffs/<lane>-<tool>.md._

| Lane | Tool (model, skill) | Status | Notes |
|---|---|---|---|
| database | Claude Code (Opus 5.5, direct) | not started | v11 in Phase 1 (`tasks.job_ref`, `job_ref_conflicts`), v12 in Phase 3 (`token_events.ingest_seq`). Backend repo only |
| backend | Claude Code (Opus 5.5, direct) | not started | Phase 1: J0 gate (AC1.1) first, then J1–J9 in backend + plugin repos; Phase 2: C0 gate (AC2.1) first, then C1–C11 in the backend repo; Phase 3: X1–X6. Shared files only via S1–S5 (SESSION-COORDINATION) |
| frontend | Claude Code (Opus 5.5, direct, frontend-design) | not started | Phase 1 Jobs view (AC1.6); Phase 3 Export panel (AC3.1, AC3.9). No Phase 2 work |
| tests-other | Claude Code (Opus 5.5, direct) | not started | Written with each backend step; mutation checks PJ*/BJ* (Phase 1), CM* (Phase 2), XM* (Phase 3). Suites locally and on hermes at every checkpoint |
| tests-e2e | Claude Code (Opus 5.5, direct) | not started | Playwright `-m browser`, own module-scoped servers; Phase 1 Jobs view, Phase 3 CSV download; mutations FM1–FM3. Run after the frontend work of the phase |
| hermes-review | Hermes (driven by Claude Code) | not started | After all three phases and tests-e2e, before review — spec in `## Hermes Code Review` (written by /new-plan Step 7b) |
| review | Claude Code (Opus 5.5, direct) | not started | Run last — full integration review (AC → test map across both repos, cross-repo contracts: env contract, tag keys, `parent_project_name`, attribution vectors, `valid_ack`; Deploy Runbook review) |

## Phase Checkpoints
_One row per phase. A phase is closed only when every column is filled. Mutation results link to the Verification Log tables._

| Phase | Local suite (backend / plugin / browser) | Hermes suite (backend / plugin: venv py + Hermes runtime py) | Mutation results | Backend commit(s) | Plugin commit(s) | Pushed to `hermes` | Date |
|---|---|---|---|---|---|---|---|
| 1 — job correlation + cross-project fix | | | PJ1–PJ7, BJ1–BJ18, FM1 | | | | |
| 2 — Claude Code producer + dedup authority | | | CM1–CM20 | | — (no plugin change) | | |
| 3 — versioned read-only export | | | XM1–XM15, FM2–FM3 | | — (no plugin change) | | |

## Drift Log
_Append here when any lane discovers the spec is wrong. Do not edit lane files mid-flight. The J0 verdict, the J2 measured tag size and cap, and every C0 rule are recorded here as they are decided._

- 2026-09-28 — Lane files written by the planner from the brief (incl. "Open questions resolved"). Planner decisions to confirm at the plan review: (1) task→job is a column on `tasks` (v11) so "a task belongs to at most one job" is enforceable, job context itself stays in tags; (2) the export snapshot uses a new `token_events.ingest_seq` (v12) because `recorded_at` is not commit-ordered and `rowid` is not VACUUM-stable; (3) CSV is built in the dashboard from the JSON export (no server CSV); (4) the Jobs view is a new nav view, the Export panel lives in it; (5) the env contract uses `TOKEN_INSPECTOR_JOB_REF` / `_WORK_TYPE` / `_JOB_ATTEMPT` and the skill passes `HERMES_WORK_TYPE` to `hermes.sh`; (6) CC producer modules are flat `cc_*.py` files so the hook runs as a plain script.

## Hermes Reviews
_Max 2 rounds per deliverable (plan, code). No round 3. Reports: Plans/o9-job-correlation-cc-producer/hermes/. This plan's brief sets 1 round for the plan and 1 for the code._

| Deliverable | Round | Date | Findings | Fixed | Known limitation | Rejected |
|---|---|---|---|---|---|---|

## Hermes Code Review
_Placeholder — the orchestrator writes this spec at /new-plan Step 7b, with PLAN_BASE (backend) and `4b069b4` (plugin) as the diff bases. Scope must cover both repos, the new `producers/claude_code/` tree, the migrations, and the coordinated shared-file diffs (S1–S5)._

## Deploy Runbook
_Owner: Claude executes; the user approves the deploy as a whole and, separately, the gateway restart, the hook installer on each machine and any repair `--apply`. Follows the O10 runbook (Plans/prompt-allowlist-safe-producer/status.md). No capture flag is enabled and `INGEST_TOKEN` stays unset. Addresses and tokens are never written here: use `$TI_URL` / env._

Upgrade (after the review lane; see Open Items O1 for per-phase vs. single deploy):

1. **Flags-off check.** On hermes: backend `STORE_TASK_PROMPTS` and `JEV_ENABLED` off, `secrets.env` sets no capture flag, `INGEST_TOKEN` unset; plugin `capture_task_prompt` off. Note the prod commits (backend `main`, installed plugin).
2. **Online DB backup + verify.** `sqlite3` online backup API (not a file copy) of the prod `DB_PATH` to `~/backups/token-inspector-pre-o9-<UTC stamp>.db`; `PRAGMA integrity_check == ok`; `token_events` and `tasks` counts equal the source. Never commit or mirror it.
3. **Backend fast-forward `main`.** On hermes, prod checkout: fetch the pushed branch and `git merge --ff-only` it into `main`. Schema changes v10 → v11 → v12 run at startup with the app's automatic, verified pre-migration backup (`<db>.bak-v10-*`); confirm it exists after the restart. Dependencies: expect no change (`requirements.txt` diff empty; the CC producer is stdlib-only); if the diff is not empty, install into the service venv before restarting.
4. **Service restart + `/api/meta`.** `systemctl --user restart token-inspector.service`; `curl -fsS <loopback>/api/meta` → `schema_version: 12`, capture/JEV `not_enabled`, `task_prompt_allowlist: "empty"`; journal clean; `GET /api/jobs?days=1` → 200; `GET /api/export/v1/events?from=…&to=…` → 200 with `schema_version: 1`. The backend with the new tag cap is now live **before** any plugin that sends the new keys (AC1.4).
5. **Repair script dry-run.** `python scripts/repair_task_parents.py --db "$DB_PATH"` → show the counts line to the user. `--apply` only with a **separate** user approval; it takes and verifies its own backup first. Record counts (no refs) in the Verification Log.
6. **Coordinated shared files** S1–S4 (three `hermes.sh` copies, `/hermes` skill) applied through SESSION-COORDINATION, if not already done.
7. **Plugin install + `hermes gateway restart`** only when no agent or JEV run is active: no `hermes -z` process, `evaluator_runs` has no `queued`/`running` row, the "hermes sırası" table shows no running job. Update the plugin source and the installed copy to the pushed commit, then `hermes gateway restart` from an external shell. Record PIDs before/after.
8. **Hook installer (S5)** on Windows and on hermes: `python producers/claude_code/install.py --dry-run` → show the diff → announce in SESSION-COORDINATION → `--apply` (backup written). Windows: the producer URL comes from env or the local config file (the tailnet HTTPS name allowed by the `remote-access.conf` drop-in), never from docs. hermes: loopback default. Confirm PossibleSkills' hooks are unchanged in the resulting file.
9. **Final check** (Verification Log):
   - AC1.2 live: one small `hermes.sh` review job through the `/hermes` flow (queue rule) → `GET /api/jobs?days=1` lists its `job_ref` with `runtime=hermes-agent`, `work_type=review`.
   - AC2.2 live: one Windows Claude Code turn → export events of the last hour include `runtime=claude-code@windows`, `producer=claude-code-hook`, the right project, distinct cache fields.
   - AC2.3 live: the next real devir run (or a short one the user approves) → `runtime=claude-code@hermes`, `work_type=devir`, `job_ref` = the devir id, project `pegadocrag`; no prompt field.
   - `tasks.prompt_text` non-null count unchanged (0); backend journal clean; plugin `counters.json` shows no new rejects.

Rollback (either part):
1. Flags stay off (they were never turned on). Uninstall the CC hooks first if they misbehave: `install.py --uninstall --apply` on the affected machine (backup written).
2. Plugin before backend if both: restore the previous plugin commit, `hermes gateway restart` with no job running. The `hermes.sh` env lines are harmless without the plugin and can stay.
3. Backend: code-only rollback to the previous `main` commit is safe on a v12 DB (new columns nullable, partial indexes); a later re-upgrade self-heals NULL `ingest_seq`. Schema rollback (`scripts/rollback_v12.sql`, then `rollback_v11.sql`) only with the service stopped and only if needed; restore from the step-2 backup as the last resort.

## Known Limitations
_Review findings the user chose not to fix — one line each: `<plan|code>-review r<N> F<k>: <what> — <why accepted>`._

- brief (resolved before planning): unauthenticated ingest from Windows until the Activation Gate — the CC producer posts over the tailnet without `X-Ingest-Token` while `INGEST_TOKEN` is unset; accepted: single user, local service, tailnet only, "auth minimal" rule; AC2.8 makes the producer ready for a token.

## Open Items
| # | Item | Owner | Blocks |
|---|---|---|---|
| O1 | Resolved (user goal, 2026-09-28): deploy **once**, after the Claude integration review (as O10), with user approval. Live checks AC1.2, AC2.2, AC2.3 are recorded as "pending deploy" until then | — | Live AC checks |
| O2 | J0 / AC1.1 gate: does `hermes -z` load plugins in its own process? If no → stop and ask (scope change; default proposal: launcher-posted job→session mapping record) | Claude (read-only), then user if no | Phase 1 J1+ |
| O3 | C0 / AC2.1 gate: CC hook stdin fields, subagent layout and parent link, streaming duplicates and final-usage line, resumed copies, `<synthetic>` lines — rules into the Drift Log | Claude (read-only) | Phase 2 C1+ |
| O4 | Tag byte cap value after the J2 measurement (default 1024, keys ≤ 20); ask if the measurement exceeds 768 B | Claude → orchestrator | J2 |
| O5 | **TODO:** owner of `AIFromScratch\scripts\hermes.sh` is not listed in SESSION-COORDINATION `## Dosya → sahip`; confirm before S1 | User | S1 |
| O6 | Owner agreement for S2, S3 (PEGA `hermes.sh` copies), S4 (`/hermes` skill) and S5 (`~/.claude/settings.json` on both machines, coexisting with PossibleSkills' planned PreToolUse hook) | Owners / user | Phase 1 live, Phase 2 deploy |
| O7 | Pricing rules or aliases for the Claude models Claude Code uses; until added they stay `unpriced` (never zero). Fix through the settings API + recost dry-run, not code | User | Complete CC cost |
| O8 | Repair `--apply` on prod after the dry-run counts | User (separate approval) | AC1.8 on prod data |
| O9 | NotebookLM: after the docs steps (incl. the tailnet-address placeholder in CLAUDE.md RP 12 and AGENTS.md gotchas) run `/sync-docs → /compress → /sync-memory`; the notebook is stale until then | Claude | — |
| O10 | PossibleSkills is told when the export is live (it does not connect before); contract = `docs/export-contract-v1.md` | TI → PS | PS export consumption |

## Verification Log
| Date | Check | Result |
|---|---|---|

Mutation checks (one mutation at a time: break the control → named test red → revert; a survivor is a missing test unless marked redundant):

| # | Phase | Control | Result | Failing test(s) |
|---|---|---|---|---|
