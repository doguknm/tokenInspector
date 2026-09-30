# Backend — O9: job correlation, cross-project child fix, Claude Code producer and a versioned read-only export

**Lane**: backend
**Tool**: Claude Code — Opus 5.5, direct (no handoff)
**Status**: not started
**Brief**: ./2026-09-28-summary.md
**Depends on**: ./database.md (v11 before Phase 1 steps J4–J7; v12 before Phase 3 step X1)

## Goal

Implement the three phases of O9 in order, each ending at a commit/push checkpoint (status.md `## Phase Checkpoints`):

1. **Phase 1** — launcher job context (`job_ref`, `runtime`, `work_type`, `job_attempt`, `producer`) in event `tags`, normalized by the backend without ever rejecting an event; one job per task; a cost-per-job API; the cross-project parent fix plus a repair script.
2. **Phase 2** — a stdlib-only Claude Code producer (`producers/claude_code/`) for Windows sessions and `claude -p` hand-off (devir) runs on hermes, with a written and enforced cross-source dedup authority.
3. **Phase 3** — a versioned, read-only JSON export (jobs, tasks, llm-call events) with a separate contract document.

**Production stays untouched by this lane.** No capture flag (`STORE_TASK_PROMPTS`, `JEV_ENABLED`, plugin `capture_task_prompt`) is enabled, `INGEST_TOKEN` stays unset (Activation Gate), and deploy, plugin install, `hermes gateway restart` and the hook installer run only through status.md `## Deploy Runbook`, with the user's approval.

## Repos, branches, baselines

| Repo | Path | Branch | Base (plan time) | Touched in |
|---|---|---|---|---|
| backend | `C:\Users\Bentego_Admin\Projects\tokenInspector` (remote `hermes`) | `feat/task-telemetry-jev-pilot` | PLAN_BASE (backend HEAD when the plan is locked; written into status.md by the orchestrator — **blocking: no Phase 1 step starts until it is recorded**, status.md O11) | Phase 1, 2, 3 |
| plugin | `C:\Users\Bentego_Admin\Projects\token_inspector` (remote `hermes`) | `feat/task-telemetry-jev-pilot` | `4b069b4` | Phase 1; Phase 2 (attribution-vector runner + fixture only) |

- Workflow: develop and test on Windows, push the branch to the `hermes` remote, run the suites on hermes in a temp worktree (never the prod checkouts). GitHub is not touched. Commit messages end with the session's attribution line.
- Git Bash ssh: `/c/WINDOWS/System32/OpenSSH/ssh.exe -o ClearAllForwardings=yes hermes` (CLAUDE.md RP 8). Always invoke `python`, never `python3`, on Windows; on hermes use the venv's `python`.
- Every step names its repo: **[B]** = backend repo, **[P]** = plugin repo, **[S]** = shared file outside both repos (coordinated, see `## Coordinated shared-file tasks`).
- Tests for each step are written with it (tests-other.md / tests-e2e.md) and its mutation checks run before the phase checkpoint.

## Shared definitions (all phases)

- **Job env contract (one contract for both producers).** The launcher exports, the producer reads:

  | Env var | Producer rule (before sending) | Backend rule (J1) |
  |---|---|---|
  | `TOKEN_INSPECTOR_JOB_REF` | send as tag `job_ref` only if it fully matches `JOB_REF_RE = ^(devir-)?[0-9]{8}-[0-9]{6}-[0-9]{1,10}$` (the launcher's own id shape `[devir-]YYYYMMDD-HHMMSS-<pid>`, max 32 chars: cannot hold a path, host, URL, secret or text — plan review r1 A-F9/B-F8), else omit | same regex, else dropped |
  | `TOKEN_INSPECTOR_WORK_TYPE` | send as tag `work_type` only if it fully matches `^[a-z0-9]{1,16}$` (shape only: no free text leaves the producer), else omit | in `WORK_TYPES` → kept; any other present value → `"other"`; absent/empty → absent |
  | `TOKEN_INSPECTOR_JOB_ATTEMPT` | send as integer tag `job_attempt` only if it fully matches `^[1-9][0-9]{0,3}$`, else omit | `type(v) is int and v >= 1` → kept, else dropped (bools, floats, strings, 0 dropped) |

  The producer adds `runtime` and `producer` itself; they never come from env.
- **Closed lists (D8 §1, §2).** `WORK_TYPES = ("brainstorm", "review", "code", "devir", "k1", "k2", "other")`; `RUNTIMES = ("hermes-agent", "claude-code@windows", "claude-code@hermes", "app")`; `PRODUCERS = ("hermes-plugin", "claude-code-hook", "app-provider")`.
- **Reserved tag keys.** `job_ref`, `runtime`, `work_type`, `job_attempt`, `producer`, plus the backend-only mark `attribution_invalid` (J1). `job_attempt` (launcher retry of the same job) is unrelated to `EventIn.attempt` (per-call retry); they are never merged.
- **Strict name rule.** Project-like names (allowlists, `parent_project_name`) are valid only if `value.strip().lower()` fully matches `^[a-z0-9][a-z0-9._-]{0,63}$`; otherwise they are **dropped**. Never pass them through the plugin's `normalize_project_name` (it falls back to `hermes`).
- **Privacy boundary (every phase).** No new field or tag carries prompt or response text, tool arguments or outputs, file paths (`cwd`, `transcript_path`, spool paths), git branch, hostnames or IPs, skill/agent names or free error text. Validation errors never echo input (existing O10 handlers; the export's own errors are static bodies). The plugin and the CC producer send no `error_message`.
- **Value rules (every new emitted or exported string; plan review r1 A-F9/B-F8).** A key allowlist is not enough; each string also has a value rule. Shapes used below:
  - `SAFE_ID_RE = ^[A-Za-z0-9_-]{1,128}$` (no `.`, `/`, `\`, `:`, `@`, whitespace: cannot be a path, URL, FQDN, IP or free text). An id that fails it is replaced by the pseudonym `"p-" + sha256(value)[:32]` (deterministic, so joins and dedup still work).
  - `MODEL_RE = ^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$` with no `//`, `\`, `@` or whitespace. A model value that fails it is exported as `null` (unknown).
  - `ERROR_CLASS_RE = ^[A-Za-z][A-Za-z0-9_]{0,63}$` applied to the last dotted component of `error_type`; failing values become `"other"` (closed class; historical free text never leaves).
  - Closed lists: `runtime`, `producer`, `work_type`, `status`, `cost_status`, `role` (unknown → `"other"`), `complexity_method` (unknown → absent).
  - Pseudonymized at the source: all Claude Code session, turn and parent ids (`sha256(raw)[:32]`, C3).
  - Residual (documented in the export contract): a single-label string that happens to equal a machine's short hostname cannot be told apart from an id or a repo name by shape. The CC producer rejects project names equal to the local hostname / FQDN or shaped like an IPv4 address (C4).
- **Evaluator rule.** JEV usage is the event with `project_name = 'token-inspector'` and `role = 'evaluator'` (`jev_scorer.EVALUATOR_PROJECT`, tags `purpose=evaluator`). The jobs API and the export always exclude it (`NOT (project_name = :evaluator_project AND role = 'evaluator')`).

---

## Phase 1 — job correlation and cross-project child fix (AC1.1–AC1.9)

Order: **J0 → J1 → J2 → J3 → J4 → J5 → J6 → J7 → J8 → J9**. J0 is a gate: nothing after it starts until its result is recorded.

### J0 — Verification gate: does `hermes -z` load plugins in its own process? [read-only on hermes] → AC1.1

1. Read-only, on hermes, at the installed Hermes Agent commit (`~/.hermes/hermes-agent`, note the commit): trace the `-z` code path from the CLI entry point to where plugins/hooks are discovered and registered. Record whether `-z` builds its agent in its own process with the plugin hooks registered (so `os.environ` of the `hermes -z` process is what the plugin sees), or hands the prompt to the running gateway.
2. No job is launched for this check, nothing is installed, no file is changed, no content is copied into the plan.
3. Record in status.md `## Verification Log`: commit, the files/functions that decide it (repo-relative paths only), and the verdict.
4. **Verdict yes** (backed by the recorded source trace) → continue with J1. **Verdict no, evidence missing or inconclusive (e.g. the path depends on runtime config that cannot be read), or any finding that contradicts an AC** → stop Phase 1 implementation and ask the user (stop-and-ask gate; never recorded as drift and continued). Admissible evidence: the installed Hermes Agent source at the recorded commit, read-only; no job run, no log or content copy. Default proposal for "no": a launcher-posted job→session mapping record (D8 §6 "separate mapping record"). Do not implement the fallback without approval.
5. Later steps assume only what the brief states: env vars set by the launcher reach the plugin *if and only if* J0 said yes. The live proof is the AC1.2 check in the Deploy Runbook.

### J1 — Reserved-key normalization [B `routes/events.py`] → AC1.3, AC1.9

1. New module-level constants in `routes/events.py`: `JOB_REF_RE`, `WORK_TYPES`, `RUNTIMES`, `PRODUCERS` (Shared definitions).
2. New `def _normalize_reserved(tags: dict) -> dict` returning a copy:
   - `job_ref`: kept only if `isinstance(v, str)` and `JOB_REF_RE.fullmatch(v)`; else the key is removed.
   - `runtime`: kept only if `v in RUNTIMES`; else removed. `producer`: kept only if `v in PRODUCERS`; else removed.
   - `work_type`: `None`/`""`/missing → key removed; a string whose `.strip().lower()` is in `WORK_TYPES` → that value; any other present value (any type) → `"other"`.
   - `job_attempt`: kept only if `type(v) is int and v >= 1`; else removed.
   - **Attribution mark (A-F8/B-F6):** if a `runtime` or `producer` key was **present** and got removed (invalid value here, or a pairing mismatch in C10), the copy gets `attribution_invalid: true`. Such an event is still inserted (never rejected) but is never counted by the jobs API or the export, and is never inferred as legacy `hermes-agent`. The mark is a reserved key: an incoming `attribution_invalid` from a producer is removed first, then set only by this rule.
   - Other keys untouched.
3. `_prepare_event` calls `_clean_tags(_normalize_reserved(body.tags))`. Normalization never raises. The 20-key check still applies after normalization; normalization never **increases** the key count (the mark is added only when at least one key was removed).
4. Runtime↔producer pairing is added in Phase 2 (C10), not here.

### J2 — Measure the worst-case plugin tag payload, then raise only the byte cap [P + B] → AC1.4

1. **[P] measure first.** New plugin test helper builds `mapping._base(...)["tags"]` for the worst case the plugin can produce **after** J3: every existing key at its longest real value (`roles` with every role the mapping can emit, `project_source` longest source name, `project_confidence` longest value, integer counters at 10^12, `complexity_version` as today) plus `job_ref` at its longest valid value (32 chars: `devir-` + 8 + 1 + 6 + 1 + 10 digits), `work_type` at 16 chars, `job_attempt=9999`, `runtime="hermes-agent"`, `producer="hermes-plugin"`, `schema` as today. Serialize exactly like the backend (`json.dumps(clean, separators=(",", ":"), sort_keys=True)`, UTF-8 bytes) and record the byte count and key count in status.md `## Verification Log`.
2. **[B] raise the cap.** In `_clean_tags`, replace the literal `512` with a module constant `TAG_BYTES_MAX`. Default value 1024 (brief weak default); if the measurement exceeds ~75 % of 1024 (768 B), stop and ask the orchestrator before picking a larger value. Keys stay ≤ 20. `project_source` / `project_confidence` stay.
3. **Pins.** Plugin test `test_worst_case_tags_fit_backend_cap` asserts measured bytes ≤ `BACKEND_TAG_BYTES_MAX` (a plugin-side constant equal to the backend value, with a comment pointing at `routes/events.py`) and key count ≤ 20, and pins the measured size (`== <measured>`), so a later tag addition is a deliberate change. Backend test `test_worst_case_plugin_tags_accepted` posts the same worst-case tags dict (shared fixture file `tests/fixtures/worst_case_plugin_tags.json`, byte-identical in both repos) and expects `inserted == 1`.
4. **Install order inside the single final deploy** (Deploy Runbook): the backend with the new cap is installed before the plugin that sends the new keys. No phase checkpoint deploys anything.

### J3 — Plugin: job tags and parent project [P `job.py` (new), `mapping.py`, `__init__.py`] → AC1.2, AC1.7, AC1.9

1. New `job.py`: `def job_tags() -> dict` reads the three env vars **once per process** (module-level cache; `hermes -z` is one process per job, and a gateway process has none of them) and returns `{"runtime": "hermes-agent", "producer": "hermes-plugin", **valid job keys}` per the Shared definitions. Invalid values are omitted silently (no log line holds the value). Never raises (wrap in the existing `try/except` style; on error return only runtime + producer).
2. `mapping._base`: merge `job_tags()` into the `tags` dict (after the existing keys, before `scrub`). Every event built from `_base` (llm success, llm error, tool events) carries them; tests assert llm events. Tag count becomes 17 of 20.
3. **Parent project for children (AC1.7):**
   - New `_session_projects = BoundedTTLMap(max_entries=2048, ttl_seconds=86400)` in `__init__.py`. `_pre_api_request` records `sid → project.name` whenever a project is resolved for a new api state.
   - `_subagent_start` stores `parent_project_name` in the child's `_subagent_state` entry = `_session_projects.get(parent_session_id)["name"]` **only if** it passes the strict name rule; otherwise the key is absent.
   - `_session_context` adds `parent_project_name` to the child context when present (next to `parent_session_id` / `parent_turn_id`); `mapping._base` emits `"parent_project_name": state.get("parent_project_name")` (None values are already stripped by `llm_success`).
   - `_cleanup_session` pops `_session_projects`.
4. No change to the O10 prompt gates, lineage, spool or sink. No new log lines.

### J4 — One job per task [B `task_store.py`] → AC1.5

1. `_upsert_task` reads the normalized `job_ref` from the stored event's tags (`_tags(event.tags_json).get("job_ref")`, already validated by J1) and passes it as `:job` in the INSERT.
2. INSERT column list gains `job_ref`; `ON CONFLICT … DO UPDATE SET` gains (database.md write rule):
   - `job_ref = COALESCE(tasks.job_ref, excluded.job_ref)`
   - `job_ref_conflicts = tasks.job_ref_conflicts + CASE WHEN tasks.job_ref IS NOT NULL AND excluded.job_ref IS NOT NULL AND excluded.job_ref <> tasks.job_ref THEN 1 ELSE 0 END`
3. Backfill (`backfill_tasks`) is unchanged (no historical job tags).

### J5 — Cost-per-job API [B `routes/jobs.py` (new), `main.py`] → AC1.5

`GET /api/jobs` (no auth, like `/api/analytics/*`; read-only).

Query: `days` (1–3650, default 30; window on `last_event_at >= now - days`: a job is listed when its last counted event is in the window, and its sums are **whole-job** sums over all its counted events, also those before the window), `runtime` (optional, one of `RUNTIMES`, else 400 static `{"error":"invalid_filter"}`), `work_type` (optional, one of `WORK_TYPES`, else same 400), `page` (≥ 1), `page_size` (1–200, default 50).

Filters select **whole jobs** (A-F22): `runtime=r` keeps the jobs whose `runtimes` contains `r`, `work_type=w` those whose `work_types` contains `w`; a kept job's totals are never reduced to the matching calls. Filtering happens after per-job aggregation (a `HAVING`/outer `WHERE` over the aggregated rows), never on events before aggregation.

Definitions:
- **Counted events:** `event_type = 'llm_request'`, excluding the evaluator rule and events with the `attribution_invalid` tag (J1).
- **Job of an event:** the job of its task (`tasks.job_ref`, joined on `(project_name, COALESCE(session_id,''), turn_id)`) when the event belongs to a task; else the event's own normalized `job_ref` tag (`json_extract(tags_json, '$.job_ref')`). Events with neither are not in any job. This is what makes "a task belongs to at most one job" hold for every call of the task, including calls whose own tag disagrees.

Response:

```json
{
  "items": [{
    "job_ref": "20260928-141024-960",
    "runtimes": ["hermes-agent"],
    "work_type": "review",
    "work_types": ["review"],
    "attempts": [1],
    "projects": ["hermes"],
    "task_count": 3,
    "llm_request_count": 41,
    "prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
    "priced_count": 41,
    "unpriced_count": 0,
    "cost_usd": 0.1234,
    "estimated_cost_usd": 0.0,
    "cost_complete": true,
    "first_event_at": "2026-09-28T14:10:25.000000Z",
    "last_event_at": "2026-09-28T14:31:02.000000Z",
    "conflict_count": 0,
    "conflict_task_count": 0
  }],
  "total": 1, "page": 1, "page_size": 50,
  "filters": {"runtimes": ["hermes-agent"], "work_types": ["review"]},
  "anomalies": {"job_ref_conflicts": 0, "job_ref_conflict_tasks": 0, "invalid_attribution_events": 0}
}
```

Field rules:
- `task_count`: distinct tasks among the job's counted events.
- `runtimes`, `work_types`, `attempts`, `projects`: sorted distinct values from the job's counted events (runtime/work_type/job_attempt from tags; events without the tag contribute nothing). `work_type` = the single element of `work_types`, or `null` when there are zero or several.
- Token sums: the four fields, never merged (`prompt_tokens` excludes cache, as stored).
- `cost_usd`: sum of `estimated_cost_usd` over `cost_status = 'priced'` only; `null` when `priced_count = 0`. `estimated_cost_usd`: sum over `partial`/`estimated`/`legacy` (0.0 when none). `unpriced_count`: `cost_status = 'unpriced'`. `cost_complete = unpriced_count == 0 and llm_request_count > 0 and no partial/estimated/legacy call`. A job with `unpriced_count > 0` is never reported as complete, and an all-unpriced job has `cost_usd: null`, never `0`.
- `first_event_at` / `last_event_at`: min/max of `COALESCE(occurred_at, recorded_at)` over counted events.
- Conflicts (A-F22, B-F15): `tasks.job_ref_conflicts` counts conflicting **events** (calls) of a task. Per job: `conflict_count` = sum of `job_ref_conflicts` over the job's distinct tasks, `conflict_task_count` = number of the job's tasks with `job_ref_conflicts > 0`. Both come from a separate subquery over `tasks` (grouped by `tasks.job_ref`), joined to the per-job event aggregate by `job_ref` — never summed after the event join (which would multiply them by the task's event count). `anomalies.job_ref_conflicts` / `anomalies.job_ref_conflict_tasks`: the same two numbers over all tasks with a counted event in the window (including tasks whose conflict came from another job's tag). `anomalies.invalid_attribution_events`: `llm_request` events with `attribution_invalid` in the window (evaluator excluded); they are stored but never counted.
- `filters`: distinct runtimes / work types present in the window, ignoring the respective filter (like complexity-matrix filter options).
- Ordering: `last_event_at DESC, job_ref ASC`. `total` = jobs matching the filters.
- The dollar value of subscription providers is a list-price estimate (AGENTS.md "What the numbers mean"); no new wording in the API, the frontend shows it.

Register the router in `main.py` next to the tasks router.

### J6 — Cross-project child hierarchy [B `routes/events.py`, `task_store.py`] → AC1.7

1. `EventIn` gains `parent_project_name: Optional[Any] = None` with a `field_validator(mode="before")` that returns `value.strip().lower()` if `value` is a `str` whose stripped-lowercased form passes the strict name rule, else `None`. It never raises, so an invalid value can never reject an event. It is **not stored** anywhere (not a column, not a tag).
2. `_upsert_task`: `parent_project = body.parent_project_name or project`; `parent_ref = task_ref(parent_project, body.parent_session_id, body.parent_turn_id)`. The parent lookup by `id` is already global.
3. The re-root `UPDATE` drops `AND project_name = :p`: `UPDATE tasks SET root_task_ref = (SELECT COALESCE(root_task_ref, parent_task_ref) FROM tasks WHERE id = :id), updated_at = :now WHERE root_task_ref = :id`.
4. Unchanged: `child` is never downgraded; the O10 child-transition null and `prompt_root_eligible` are untouched (no child stores a prompt; O10 tests pass unchanged).
5. Absent or invalid `parent_project_name` gives exactly today's `parent_ref` (child's own project).

### J7 — Repair script for existing cross-project rows [B `scripts/repair_task_parents.py` (new)] → AC1.8

Stdlib-only (`sqlite3`, `hashlib`, `argparse`), same style as `scripts/purge_task_prompts.py`.

- CLI: `python scripts/repair_task_parents.py --db PATH [--apply] [--backup-dir DIR]`. Default is **dry-run**. Output is counts only, never refs, names, sessions or paths: `dangling=<n> relinkable=<n> ambiguous=<n> unmatched=<n> cyclic=<n> depth_exceeded=<n> unresolved_ancestor=<n> relinked=<n> roots_updated=<n> mode=dry-run|apply` (one count per skipped class; A-F12).
- Algorithm — one pure function `plan_repair(conn) -> Plan` used by both modes (read-only):
  1. `dangling` = rows with `parent_task_ref IS NOT NULL AND parent_task_ref NOT IN (SELECT id FROM tasks)`.
  2. Candidates: for each distinct child project `P` among them, hash every distinct `(session_id, turn_id)` of `tasks` rows in **other** projects: `task_ref(P, s, t)` (same formula as `task_store.task_ref`: `sha256(P \x1f s \x1f t)[:32]`). A dangling row whose `parent_task_ref` equals exactly one such hash → candidate relink to that row's `id`; more than one → `ambiguous`; none → `unmatched`. A match to the row itself is refused as `cyclic`.
  3. **Proposed graph before any write:** effective parent of every task = its candidate new parent if it has one, else its current `parent_task_ref`. For each candidate, walk up the **effective** graph (so a parent that is itself being repaired is followed through its own relink) with a visited set and depth ≤ 64. Outcomes: reaches a row with no parent → root = `COALESCE(that row's root_task_ref, its id)` (the `_upsert_task` rule), candidate accepted; revisits a node → the whole connected component is `cyclic`; depth > 64 → the component is `depth_exceeded`; hits a parent ref that exists in no row and is not being repaired → `unresolved_ancestor`. Every task in a refused component keeps its current values. Roots are thus independent of processing order (deterministic).
  4. Re-roots: every row whose `root_task_ref` equals the **old** wrong ref of an accepted child gets that child's new root (`roots_updated`).
  5. **Postconditions** (checked on the plan and again after apply, inside the transaction): no accepted row's parent is missing; every accepted row's root has no parent; no cycle in the effective graph; counts add up (every class counts dangling rows only; `relinkable` = candidates before step 3, `relinked` = accepted; `dangling = relinked + ambiguous + unmatched + cyclic + depth_exceeded + unresolved_ancestor`). A failed postcondition → rollback, exit non-zero.
- `--apply` (A-F13):
  1. Online backup first with `sqlite3.Connection.backup` to `<backup-dir or db dir>/<db name>.bak-repair-<UTC stamp>`. Verify it against **its own** coherent snapshot, not against a live DB that may be ingesting: `PRAGMA integrity_check == ok` on the backup, and for `tasks` and `token_events`: count read from the source just before the backup ≤ count in the backup ≤ count read from the source just after it (no app path deletes these rows). Any failure → exit non-zero **before** any write.
  2. One `BEGIN IMMEDIATE` transaction with `PRAGMA busy_timeout=5000` (the service may be running): **re-run `plan_repair` inside the write transaction** (the dry-run/analysis result is never applied), apply the relinks and re-roots of that fresh plan, `updated_at` = now, check the postconditions, commit. `hierarchy_status` is not touched (rows are already `child`). From Phase 3 on (X1), the same transaction increments `export_state.revision` when anything changed (if the table exists).
- Idempotent: a second run finds `dangling=0` (or only refused rows) and changes nothing.
- Prod use: dry-run counts are shown to the user at deploy; `--apply` on prod only with separate user approval (Deploy Runbook).

### J8 — Launcher and skill changes [S, coordinated] → AC1.2

See `## Coordinated shared-file tasks` S1–S4. They can be applied any time after SESSION-COORDINATION agreement (exported env vars are ignored until the new plugin is installed); S1 additionally waits for the owner confirmation in status.md O5.

### J9 — Documentation (Phase 1) [B + P] → Every phase

- `docs/adr/003-job-correlation-contract.md` (new): env contract, reserved tag keys and their normalization (incl. `attribution_invalid`), closed lists and value rules, one-job-per-task rule and conflict counters (events vs. tasks), install order inside the single deploy (backend cap before plugin), J0 verdict, `job_attempt` ≠ `attempt`, review jobs land in project `hermes` (out-of-scope note).
- `AGENTS.md`: Module map (`routes/jobs.py`, `scripts/repair_task_parents.py`), Tasks section (job_ref, cross-project parent via `parent_project_name`), API families (`GET /api/jobs`), "What the numbers mean" (job cost semantics; no job-duration event), Gotchas: replace the "Known defect (Q1, not fixed)" entry with the fix and the repair script; add "tags cap is `TAG_BYTES_MAX`; measure before adding tag keys"; **replace the tailnet host in the `TrustedHostMiddleware` gotcha with a placeholder** (`https://<tailnet-name>` → the service's loopback bind), per the brief's resolved decision.
- Project `CLAUDE.md`: **RP 12**: replace the literal tailnet host with the same placeholder; add an RP for "job tags missing on events" (symptoms: `/api/jobs` empty after a hermes.sh run; root cause: old plugin / gateway not restarted / J0 path / launcher copy not updated; check: event tags of the session, plugin version, `hermes gateway restart`).
- `README.md`: jobs API, env contract (names only, no values), install-order note (backend before plugin inside the one deploy).
- `ARCHITECTURE.md`: job correlation in the data flow; task→job rule.
- `CHANGELOG.md` `## Unreleased`: job tags, tag cap, `/api/jobs`, cross-project parent fix, repair script, Jobs view.
- `.github/workflows/ci.yml`: `scripts/*.py` is already compiled and new tests run in the existing jobs; confirm and note "no change" in status.md, or add what is new.
- Plugin `README.md` [P]: the job env vars, new tags, `parent_project_name`.

**Phase 1 checkpoint** (status.md): local + hermes suites green in both repos, mutation checks PJ*/BJ* recorded, commits in both repos, push to `hermes`. Commit + push only — no deploy.

---

## Phase 2 — Claude Code producer and dedup authority (AC2.1–AC2.11)

Order: **C0 → C1 … C9 → C10 → C11**. C0 is a gate. Everything in this phase is in the **backend repo** under `producers/claude_code/` (brief Q2=C) except C10 (backend `routes/events.py`), the coordinated settings changes (S5) and one **plugin-repo** addition: the attribution-vector runner `tests/test_attribution_vectors.py` plus its byte-identical `tests/fixtures/attribution_vectors.json` (C4; committed and pushed at the Phase 2 checkpoint — plan review r1 B-F16).

Layout (flat modules, no package imports, so `python <path>/cc_hook.py` works from a hook command; module names are prefixed to avoid collisions):

| File | Responsibility |
|---|---|
| `producers/claude_code/cc_hook.py` | entry point for Stop / SubagentStop / SessionEnd; watchdog; fail-open |
| `producers/claude_code/cc_config.py` | URL, token, aliases, state dir; job env (Shared definitions) |
| `producers/claude_code/cc_transcript.py` | incremental JSONL reader, per-message grouping, dedup rules from C0 |
| `producers/claude_code/cc_events.py` | record → event dict (explicit field allowlist) |
| `producers/claude_code/cc_attribution.py` | project name: alias → origin slug → git root → `claude-code` |
| `producers/claude_code/cc_state.py` | per-transcript cursor files (offsets and ids only), bounded |
| `producers/claude_code/cc_client.py` | batch POST with `urllib.request`, ack validation |
| `producers/claude_code/install.py` | settings.json merge: dry-run/diff, apply with backup, uninstall |
| `producers/claude_code/README.md` | install, config, privacy allowlist, uninstall |

### C0 — Verification gate: Claude Code transcript and hook facts [read-only, local] → AC2.1

Read-only, on local transcripts under the Windows `~/.claude/projects/` tree (and, if convenient, one on hermes). **No content is copied**: only field names, line types, counts, sizes and structural relations are recorded. Record in status.md `## Verification Log` and write the chosen rule for each item into the Drift Log as "C0 rule".

Admissible evidence per fact (A-F18): items 2–9 — a throwaway local script that scans transcripts and prints only key names, line types, counts, byte sizes and id-equality relations (never values); item 1 — the official Claude Code hooks documentation **plus** one scratch run of `claude -p` in a temp directory with `--settings <temp settings file>` whose hooks write only the stdin **key names** to a temp file (the global `~/.claude/settings.json` is not touched; the temp files are deleted afterwards).

1. Hook stdin JSON fields for `Stop`, `SubagentStop` and `SessionEnd` (names only: e.g. whether `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `stop_hook_active`, an agent transcript path or agent id exist). Also: the stdin encoding on Windows with a non-ASCII profile path (read `sys.stdin.buffer` and decode UTF-8 either way).
2. Subagent transcript layout: separate files (location relative to the session transcript) **or** sidechain lines in the main transcript; which field links a subagent run to the parent turn (the Task `tool_use` id, `parentUuid`, an agent id); which stable id a subagent run has (for the child `session_id` / `turn_id`, C8).
3. Streaming duplicates: how many lines share one `message.id`, whether they share `requestId`, which line holds the final `usage` (last line / max `output_tokens`), whether a group's lines are contiguous or can interleave with other groups, and whether every line of a message is on disk before `Stop` fires. From this, an **observable finality rule** for a group (C2 item 3).
4. Resumed sessions: whether copied earlier messages keep their `message.id` and `requestId`, and under which session id/file they appear.
5. `<synthetic>` model lines: their shape and usage (expected: skipped, never counted).
6. The user-turn boundary: which line type starts a user turn (a `user` line that is not a tool result) and which id is stable (line `uuid`).
7. Error records: how an API error or an interrupted call appears (a marker field on the assistant line, a separate line type), and whether it carries `usage`.
8. The largest assistant usage line seen (bytes) and the largest line of any type, to confirm the C2 size limits.
9. Whether a transcript file is ever rewritten or shortened in place (e.g. on `/clear` or compaction) rather than appended.

**Stop-and-ask gate (A-F18).** The rules below are written for the expected facts. If a fact has no admissible evidence, or C0 contradicts an AC (e.g. no reliable parent link → AC2.5; no observable finality rule → AC2.4; a transcript that is rewritten in place in a way C5's identity check cannot detect → AC2.7), Phase 2 implementation stops and the user decides; it is not recorded as drift and continued. A C0 fact that only refines a rule within the ACs (e.g. a different field name) is recorded in the Drift Log as "C0 rule" before coding (not a lane-file edit).

### C1 — Configuration [B `cc_config.py`] → AC2.8, AC2.2

- URL: env `TOKEN_INSPECTOR_URL`, else `url` in the config file, else the loopback default used by the plugin. On Windows the tailnet HTTPS URL is set in env or the config file; it is never written to repo files or docs.
- Token: env `TOKEN_INSPECTOR_API_KEY` (same variable as the plugin), else the first line of a local secret file (`token_file` in the config, default `~/.config/token-inspector/ingest-token`). Unset → no header (works while `INGEST_TOKEN` is unset). The token is never logged, printed, stored in state or put in an exception message.
- Config file (optional): `~/.config/token-inspector/claude-code.json` with `url`, `token_file`, `project_aliases` (object: absolute workspace root → project name). Env `TOKEN_INSPECTOR_PROJECT_ALIASES` (same JSON object) overrides the file. Invalid alias names (strict name rule) are dropped.
- State dir: `%LOCALAPPDATA%\token_inspector_cc\` on Windows, `~/.local/state/token_inspector_cc/` elsewhere.
- Runtime: `claude-code@windows` when `sys.platform == "win32"`, `claude-code@hermes` on Linux; on any other platform the hook sends nothing and exits 0 (an event without a runtime would otherwise be ambiguous; A-F8).
- Job keys: the Shared-definitions env contract (same regexes as the plugin's `job.py`).

### C2 — Transcript reader [B `cc_transcript.py`] → AC2.2, AC2.4, AC2.5

1. Read from the stored byte offset to the end of the **last complete line** (ending in `\n`); at most 4 MiB and 1000 assistant records per window (the same invocation reads further windows while its send budget lasts, C7; the next hook continues). A line that is not valid JSON is skipped and counted (`malformed_lines`), never fatal.
   - **Oversized line (A-F5):** a complete line longer than the 4 MiB window is read in 1 MiB chunks up to `OVERSIZED_LINE_MAX = 32 MiB` and parsed normally (it ends the window). A line beyond 32 MiB is skipped by scanning to its `\n` without keeping it, counted `oversized_lines`, and the offset moves past it. C0 item 8 confirms that assistant usage lines are far below 32 MiB; if not, stop and ask.
   - A partial last line (no `\n` yet) is never consumed.
2. Assistant usage records: lines with `message.usage` and a `message.id`. Lines whose `message.model == "<synthetic>"` are skipped (C0 item 5). Lines without `message.id` are skipped and counted.
   - Error records (C0 item 7, A-F19): a record with usage that C0 identifies as an API error/interrupted call is sent with `status="error"` and `error_type="api_error"` (closed class); otherwise `status="success"`. Calls that leave no usage record are not measured and are not sent; they are counted locally (`records_without_usage`) and named as a coverage limit in `producers/claude_code/README.md` and AGENTS.md Coverage.
3. **Group finality (A-F3/B-F2).** Group by `(message.id, requestId)`; the record kept per group is the line chosen by C0 item 3 (expected: the last line of the group in file order). A group is **final** only when (a) a later complete line that does not belong to it has been read (another group or a user line), or (b) the read reached EOF on a Stop / SubagentStop / SessionEnd hook and C0 item 3 confirmed that all lines of a message are on disk before that hook fires. A group that is not final is **never emitted in this window**: the window's emit set ends before it, and the checkpoint can never pass its first line (C6), so the next read sees the whole group again. A split across windows, batches or hooks can therefore never commit an early line and later discard the final one as a duplicate. If C0 shows groups interleave, the checkpoint rule "earliest unacknowledged group start" (C6) still holds; if C0 gives no observable finality rule, stop and ask (C0 gate).
4. Turn tracking: the reader keeps the `uuid` of the latest user-turn line (C0 item 6) seen at or before each record; that uuid is the record's `turn_id` (pseudonymized in C3). Because the reader starts mid-file, the cursor state also stores the turn uuid in effect at the checkpoint (it is an id, not content).
5. Each emitted record carries its window-internal `start_offset` (first line of its group) for the checkpoint rule; offsets never leave the machine.
6. Nothing but the fields named in C3 is read out of a line into memory structures that leave the function; message `content` is only inspected to count `tool_use` blocks.

### C3 — Event mapping (explicit allowlist) [B `cc_events.py`] → AC2.2, AC2.4, AC2.6

Each emitted event is built from this allowlist and nothing else:

| Field | Value |
|---|---|
| `client_event_id` | `"cc-" + sha256(message.id + "\x1f" + (requestId or ""))[:32]` — raw provider ids never leave the machine |
| `event_type` | `"llm_request"` |
| `occurred_at` | the record line's `timestamp` (ISO-8601, must include a timezone; else omitted) |
| `provider` | `"anthropic"` |
| `model` | `message.model` only if it fully matches `^claude-[a-z0-9.-]{1,64}$`; else `"unknown"` (value rule; stays `unpriced`) |
| `session_id` | `sha256(raw id)[:32]` of the CC session id (main) or of the subagent's own id (child, C8) — pseudonymized at the source (A-F9/B-F8) |
| `turn_id` | `sha256(raw uuid)[:32]` of the C2 item 4 uuid (children: C8) |
| `task_hierarchy` | `"root"` (main transcript) / `"child"` (subagent) |
| `parent_session_id`, `parent_turn_id`, `parent_project_name` | children only (C8); the two ids pseudonymized with the same function, so they equal the parent's own `session_id` / `turn_id` |
| `role` | `"primary"` or `"subagent"` (never an agent name) |
| `prompt_tokens` | `usage.input_tokens` (Anthropic reports it without cache) |
| `cache_read_tokens` | `usage.cache_read_input_tokens` (0 if absent) |
| `cache_creation_tokens` | `usage.cache_creation_input_tokens` (0 if absent) |
| `completion_tokens` | `usage.output_tokens` |
| `input_tokens_include_cache` | `false` |
| `status`, `error_type` | `"success"`, or `"error"` + `"api_error"` for a C0-identified error record (C2 item 2) |
| `finish_reason` | `message.stop_reason` if it fully matches `^[a-z_]{1,32}$`, else omitted |
| `tool_call_count` | number of `tool_use` blocks in the kept record's content (count only) |
| `tags` | `runtime`, `producer="claude-code-hook"`, `job_ref`, `work_type`, `job_attempt` (when valid), `project_source` (`alias` / `git_remote` / `git_root` / `fallback`), `schema="claude-code-producer-v1"` |

Never sent: any text, `cwd`, `transcript_path`, `gitBranch`, `version`, `userType`, hostnames, tool names or inputs, skill or agent names, `request_tool_names` (stays null), `error_message`, prompt fields (`task_prompt_text`, `prompt_text`, `prompt_hash` of content). D10: the CC producer never sends prompt text under any condition. A final allowlist filter (`{k: v for k in ALLOWED_FIELDS}`) runs on every event right before serialization.

### C4 — Project attribution [B `cc_attribution.py`] → AC2.2, AC2.3

Order for the hook's `cwd` (read from stdin, used only locally): configured alias (longest matching workspace-root prefix) → sanitized git `origin` slug → git root directory name → fallback `claude-code`.

- Git data is read from files (stdlib): walk up from `cwd` to the directory holding `.git` (a directory, or a `.git` file with `gitdir:`), read `remote "origin"` `url` from its `config`. No `git` subprocess.
- Slug sanitization and name normalization must give the same result as the plugin's `project.py` for the same inputs. Parity: a shared vector file `tests/fixtures/attribution_vectors.json` (remote URLs incl. scp-like, https with credentials, `.git` suffix, uppercase, trailing slash; directory names) is byte-identical in both repos; each repo's test runs its own implementation against it; a backend test compares the file with the plugin repo's copy when that sibling repo exists (skip otherwise).
- The devir clone on hermes (`~/Projects/PEGADocRagAgent-devir`) resolves to `pegadocrag` through its origin slug (ADR-002 R2; the backend does not recognize `-devir`).
- Only the resolved name leaves the function; the path, the remote URL and the source path are never returned or logged.
- Value rule (A-F9/B-F8): a resolved name that equals the local hostname (`socket.gethostname()`, lowercased, and its first label) or the FQDN (`socket.getfqdn()`), or that is shaped like an IPv4 address, is discarded and resolution continues with the next source (ultimately `claude-code`).

### C5 — Cursor state [B `cc_state.py`] → AC2.7

- One file per transcript: `<state dir>/<sha256(abs transcript path)[:32]>.json` = `{"offset": int, "turn_uuid": str|null, "file_id": [st_dev, st_ino], "pending": bool, "updated_at": iso}`. No path, no content, no token (`turn_uuid` is an id; `file_id` is two integers).
- **Per-transcript lock (A-F6/B-F1).** Before reading, the hook creates `<hash>.lock` with `os.open(…, O_CREAT | O_EXCL)`. If it exists and its mtime is younger than `2 × HOOK_BOUND_S`, the hook skips this transcript (fail-open: the lock holder or the next hook continues). An older lock is stale (its holder was killed): it is removed and creation is retried once. The lock is removed in `finally`; the watchdog's `os._exit` leaves it, and the stale rule recovers it.
- **Monotonic, atomic checkpoint.** Written after each acknowledged batch (C6): under the lock, re-read the state file and write only if the new `offset` is greater than the stored one (never regress); write to a unique temp file `<hash>.<pid>.<random hex>.tmp` in the state dir, then `os.replace`. A `PermissionError` from `os.replace` on Windows (file briefly open elsewhere) → skip this write (the next checkpoint or hook retries). Orphan `.tmp` files older than 1 h are removed during pruning.
- **Truncation / replacement (A-F5/B-F4).** Before reading, compare `os.stat` of the transcript with the state: if `st_size < offset` or `(st_dev, st_ino)` differs from `file_id`, the file was shortened or replaced → reset `offset = 0`, `turn_uuid = null` (re-sending is safe: idempotent insert, cross-project CC index). Content-free: no bytes of the file are stored for this check. (On Windows, `st_ino` is the NTFS file index; if it is 0 the check falls back to size only — recorded in C0 item 9.)
- **Backlog flag.** `pending = true` when the last read stopped at a window/budget limit instead of EOF; `false` when it reached EOF.
- Bounded: at most 512 state files; on write, the oldest by `updated_at` beyond 512 are deleted, **skipping any file whose lock exists and is not stale** and the current transcript's file. A state file > 4 KiB or unreadable is treated as absent (re-read from 0 is safe: idempotent insert).

### C6 — Client and ack [B `cc_client.py`] → AC2.4, AC2.7, AC2.8

- `POST {url}/api/events/batch`, header `X-Project-Name: <project>`, `Content-Type: application/json`, `X-Ingest-Token` only when a token is configured. Batches ≤ 200 events; one project per batch.
- Timeout 2 s per request (`urllib.request.urlopen(..., timeout=2)`).
- `valid_ack(body, n)`: same rules as `token_inspector_client.valid_ack` (dict; `inserted`, `duplicates`, `rejected` each `type(v) is int`, `>= 0`, summing to `n`). Copied, not imported (stdlib-only), with a test that both functions agree on the same vectors.
- **Per-batch checkpoint (A-F4).** Batches are sent in file order. After each batch that returned 2xx with a valid ack, the checkpoint advances (C5) to the **start offset of the earliest group not yet acknowledged** (the first group of the next batch, or the first non-final group, C2 item 3), or to the window end when everything in the window was acknowledged and final; `turn_uuid` = the turn in effect at that offset. A failure (401, 403, 422 whole request, 5xx, timeout, connection error, malformed ack) stops sending for this invocation with no further advance, exit 0. So an interruption (failure, watchdog, kill) keeps every earlier acknowledged batch, and repeated interruptions still make progress batch by batch.
- **Rejected items (B-F3).** An ack is valid when `inserted + duplicates + rejected == n`. `inserted` and `duplicates` are delivered. `rejected` items are per-item validation failures, deterministic for the same bytes, so a retry can never succeed and holding the cursor would stall every later record: they are **dropped deliberately** and counted (`rejected` in `counters.json`), and the checkpoint advances past them. Loss is visible and bounded to events the backend refuses; C3 builds every event from validated shapes so the expected count is zero (contract test against the real app, tests-other).
- Local counters (in a small `counters.json` in the state dir: `sent`, `duplicates`, `rejected`, `failed_posts`, `malformed_lines`, `skipped_records`, `records_without_usage`, `oversized_lines`, `lock_skips`, `truncation_resets`) — integers only; written with the same unique-temp + `os.replace` rule, best effort (a lost counter update is acceptable, never a crash).

### C7 — Hook entry and fail-open bound [B `cc_hook.py`] → AC2.7

- **Latency contract (A-F23).** The hook runs synchronously in Claude Code's hook slot; it may delay Claude Code by at most `HOOK_BOUND_S = 5` s (watchdog), with the installed hook `timeout: 10` as the backstop. "Never blocks" in the ACs means "never longer than this bound"; tests assert the bound, not zero waiting. The 5 s value is Open Item O13 (user confirmation).
- A watchdog `threading.Timer(HOOK_BOUND_S, os._exit, args=(0,))` with `HOOK_BOUND_S = 5` starts first (daemon thread, before stdin is read, so a blocked stdin is also bounded); the whole body runs in `try/except BaseException` → exit 0.
- Reads stdin as bytes (`sys.stdin.buffer.read()`), decodes UTF-8, parses JSON. Any error → exit 0.
- **Send budget.** No new POST starts after `SEND_DEADLINE_S = 2.5` s from start (a POST has a 2 s timeout), and no new window is read after it; the remaining time covers the last checkpoint write. Work left over is marked `pending` (C5).
- **Bounded drain (A-F5/B-F4).** After its own transcript, if budget remains, the hook lists the `*.jsonl` files in the same directory as its transcript (at most 200 entries; subagent files in their C0 location likewise) and, for any whose state file exists with `pending: true` (or whose size exceeds the stored offset), processes one window under that file's lock. So a SessionEnd backlog larger than one invocation is drained by later hooks of the same project directory; the state still holds no path (candidates are found by hashing listed paths). The remaining gap — a directory where no hook ever fires again — is documented in the producer README.
- Writes nothing to stdout or stderr (a Stop hook's output or exit code 2 can change Claude Code behaviour). Exit code is always 0.
- `stop_hook_active` (if present per C0) does not change behaviour: the hook never asks Claude Code to continue.
- Installed with a hook `timeout` of 10 s (C9), so Claude Code bounds it even if the watchdog failed.

### C8 — Subagents as child tasks [B `cc_transcript.py`, `cc_events.py`] → AC2.5

Per the C0 item 2 rule. Expected shape: each subagent run has its own transcript (or sidechain segment) and its own id. Then:
- child `session_id` = the (pseudonymized) subagent's own id (so each subagent run is its own task; a subagent run is one child task);
- child `turn_id` = the same value as the child `session_id` (A-F19): a subagent transcript may have no user-turn line, and one run is one task, so the run id is the stable turn identifier; it never depends on reading position;
- `parent_session_id` = the main session id; `parent_turn_id` = the `turn_id` of the parent turn in which the subagent was started (resolved through the C0 link field); both pseudonymized with the same function as the parent's own ids;
- `parent_project_name` = the resolved project (same cwd);
- `task_hierarchy = "child"`, `role = "subagent"`.
If C0 finds no reliable link to the parent turn (contradicts AC2.5), Phase 2 stops and the user decides (C0 gate); children are not sent unlinked by default.

### C9 — Installer [B `install.py`] + S5 → AC2.9

- `python install.py [--settings PATH] [--dry-run | --apply] [--uninstall]`. Default `--dry-run`: prints a **content-free operation summary** and changes nothing (A-F10), e.g. `add Stop=1 SubagentStop=1 SessionEnd=1; already_present=0; foreign_hook_entries_preserved=<n>; other_top_level_keys_preserved=<n>`. It never prints the settings file, a diff of it, or any other hook's command (they may hold credentials, URLs or paths). The user reviews our own entries in the producer README (their exact shape); the complete before/after stays in the local backup.
- Entries added under `hooks.Stop`, `hooks.SubagentStop`, `hooks.SessionEnd`: one `{"matcher": "", "hooks": [{"type": "command", "command": "<python> \"<abs path to cc_hook.py>\"", "timeout": 10}]}` each (`<python>` = `python` on Windows, `python3` on hermes, per CLAUDE.md and hermes facts).
- **Ownership (A-F11).** An entry is ours only if its `command` string is **exactly equal** to the command this installer builds for this machine (same interpreter token, same absolute path, same quoting) — never a suffix match. Uninstall removes only those hook objects; in a mixed container (a matcher group holding our hook and foreign hooks) only our hook object is removed and the container stays; a container is removed only when it becomes empty and was ours.
- Merge is idempotent (a second `--apply` is a no-op), preserves every other key and every other hook entry (including PossibleSkills' PreToolUse coordination hook and any other Stop hook), keeps key order, writes UTF-8 with 2-space indent and a trailing newline.
- **Atomic write with unchanged-source check (A-F11).** Read the file bytes and their sha256; compute the result; immediately before writing, re-read the bytes: if the hash differs (another session edited it), abort with exit 1 and a fixed message, nothing written. Otherwise write a unique temp file in the same directory and `os.replace` it over the original.
- `--apply` writes a backup `settings.json.bak-ti-<UTC stamp>` next to the file first. The backup is local only: never committed, mirrored, synced or printed. Invalid JSON in the existing file → abort without writing (exit 1 with a fixed message, no file content echoed).
- `--uninstall` removes only our entries (per the ownership rule); same dry-run default (summary only), backup and atomic write.
- Running it on the real `~/.claude/settings.json` on Windows or hermes is S5 (coordinated, user-approved). On Windows the user settings file Claude Code reads is `C:\Users\Doğukan Mutlu\.claude\settings.json` (non-ASCII profile path; PossibleSkills measured 2026-09-28: no `hooks` key yet), not `C:\Users\Bentego_Admin\.claude\settings.json` (only `enabledPlugins`). The installer resolves `~` via `Path.home()` and reads/writes the file as UTF-8 bytes; the S5 step confirms the resolved path in its summary without printing file content.

### C10 — Dedup authority: enforcement in the backend [B `routes/events.py`] + ADR → AC2.10

1. Extend `_normalize_reserved` (J1) with the pairing rule: `hermes-agent` requires `producer == "hermes-plugin"`; `claude-code@windows` and `claude-code@hermes` require `producer == "claude-code-hook"`; `app` requires `app-provider`. On a mismatch or a missing producer, `runtime` **and** `producer` are dropped and the event gets `attribution_invalid: true` (J1 mark). The event is still inserted (never rejected), but it is not counted by the jobs API or the export and is never inferred as legacy `hermes-agent` (A-F8/B-F6). Genuine legacy = an event with no `runtime`, no `producer` and no mark.
2. **Cross-project CC dedup (A-F7/B-F5).** `_insert_event`: when `client_event_id` starts with `cc-`, the insert uses `ON CONFLICT DO NOTHING` without a conflict target (so a conflict on either the per-project index or `ux_token_events_cc_client_event` is a duplicate, never an `IntegrityError`), and the stored-row lookup is by `client_event_id` alone. A resumed copy resolved to another project (alias or cwd change) therefore returns `duplicate` and is counted once, in the project of its first arrival. Other producers keep today's per-project behaviour. ADR-004 reserves the `cc-` prefix for the CC producer.
3. Within CC, only transcript usage records produce events (C2); hook stdin data is used only to locate the transcript and the project. This is structural and asserted by a test (no event is built from hook stdin fields).
4. `docs/adr/004-cross-source-dedup-authority.md` (new): one counting producer per runtime (table above); invalid pairs are marked and not counted; `client_event_id` rule for CC (`cc-` + hash of `message.id` + `requestId`, never the session; unique across projects, first arrival owns the project); why resumed copies and streaming duplicates count once; that the plugin and the CC hook observe disjoint runtimes (a `claude -p` process started from a hermes job is `claude-code@hermes`, never `hermes-agent`); the consumer dedup rule for the export (Phase 3 links here).

### C11 — Documentation (Phase 2) [B] → AC2.11, Every phase

- `AGENTS.md` "What the numbers mean" → **Coverage** updated: producers now `hermes-plugin` (hermes-agent) and `claude-code-hook` (claude-code@windows, claude-code@hermes); not measured: application provider calls; interactive CC sessions on hermes are included as `claude-code@hermes` with no `job_ref`. Module map: `producers/claude_code/`. Attribution order for CC.
- `producers/claude_code/README.md`: install/uninstall, config, env contract, allowlist of sent fields, D10 statement, fail-open behaviour.
- `README.md`, `ARCHITECTURE.md` (second producer in the data flow), `CHANGELOG.md`, `CONTRIBUTING.md` (how to run the producer tests), `.github/workflows/ci.yml` (`py_compile producers/claude_code/*.py`; the tests run in the existing job).
- Project `CLAUDE.md`: RP for "CC events missing" (hook not installed / URL or Host allowlist / token / cursor file; check commands without printing the token).

**Phase 2 checkpoint** (status.md): backend local + hermes suites (producer tests run on both), plugin suite (the attribution-vector runner), mutation checks CM* recorded, commits in **both** repos (backend + plugin attribution-vector runner and fixture), push both. Commit + push only — no deploy.

---

## Phase 3 — versioned read-only export (AC3.1–AC3.9)

Order: **X1 → X2 → X3 → X4 → X5 → X6**.

### X1 — Ingest sequence assignment [B `routes/events.py`] → AC3.4

`_insert_event` adds `ingest_seq = (SELECT last_seq + 1 FROM export_state WHERE id = 1)` to the INSERT values (scalar subquery in the same statement) and, when the row was really inserted, runs `UPDATE export_state SET last_seq = last_seq + 1 WHERE id = 1` in the same transaction (database.md "Assignment on insert"; persistent high-water, A-F2). The startup self-heal `assign_missing_ingest_seq` is called from `database.init_db` inside the migration transaction (database.md). `ingest_seq` is never returned by `/api/events` or the ingest ack (exclude it like `prompt_text`).

Revision bumps (A-F1/B-F7; database.md "Revision and epoch"): `routes/settings.py` recost with `dry_run=false` and `changed > 0` runs `UPDATE export_state SET revision = revision + 1 WHERE id = 1` in its commit; `scripts/repair_task_parents.py --apply` does the same in its write transaction when it changed rows and the table exists. Any future maintenance that deletes or rewrites `token_events` must do the same (AGENTS.md gotcha).

### X2 — Export router [B `routes/export.py` (new), `main.py`] → AC3.1, AC3.3, AC3.4

`GET /api/export/v1/jobs`, `GET /api/export/v1/tasks`, `GET /api/export/v1/events`. No auth dependency, no writes, works with `INGEST_TOKEN` unset (Host allowlist still applies). Does **not** reuse `GET /api/events`.

Query parameters are declared as plain `Optional[str]` and validated by hand so every error is a documented static body (no FastAPI 422 with input):

| Param | Rule |
|---|---|
| `from` | required; ISO-8601 with `Z` or an offset; normalized to UTC `...Z`; inclusive |
| `to` | required; same; exclusive; `from < to`; span ≤ 92 days |
| `limit` | optional; integer 1–1000; default 500 |
| `cursor` | optional; opaque; must decode and match dataset, `from`, `to` |

Cursor: URL-safe base64 of compact JSON `{"v":1,"d":<dataset>,"f":<from>,"t":<to>,"s":<as_of>,"r":<revision>,"e":<epoch>,"k":<last key>}`. On the first page (no cursor) `as_of`, `revision` and `epoch` are read from `export_state` in one read; every page of that export uses the same values. On a later page, if the current `revision` or `epoch` differs from the cursor's, the response is the static 409 `snapshot_expired` (X5): the consumer restarts from page 1 (A-F1/B-F7; cursor lifetime rule). Keys: events → `ingest_seq`; tasks → `task_ref`; jobs → `job_ref`. Keyset: `key > :k ORDER BY key LIMIT :limit + 1` (one extra row decides `complete`).

Snapshot rule (all datasets): only events with `ingest_seq <= as_of` are read, and **membership and sums are derived only from those events** — never from live `tasks` columns that later events can change:
- **Snapshot job of a task** (A-F1): the `job_ref` tag of the task's lowest-`ingest_seq` event that carries a valid `job_ref`, among its snapshot events. (This equals `tasks.job_ref` once all events are in the snapshot, because first-write-wins happens in ingest order in the same transaction; but a task that gains its first job after `as_of` has no job in this snapshot.) **Job of an event** = the snapshot job of its task when it belongs to a task, else its own `job_ref` tag.
- Snapshot conflicts: an event whose own valid `job_ref` differs from its task's snapshot job is a conflicting event; `conflict_count` / `conflict_task_count` in the jobs dataset are computed from snapshot events this way (not from `tasks.job_ref_conflicts`).
- Counted events exclude the evaluator rule and `attribution_invalid` events.
- Concurrent ingest (sequence > `as_of`) never changes membership or sums within one export, and the sequence is never reused (persistent high-water), so concatenated pages equal the full result with no duplicates or gaps. Mutations of already-snapshotted data (recost, repair) bump `revision` and expire open cursors instead of changing later pages silently.
- **Live attributes** (read at page time, may differ between pages, documented as such): on tasks `hierarchy_status`, `parent_task_ref`, `root_task_ref` (a later event can re-root a task), `completion`, `completed_at`, `updated_at`; on events `complexity`, `complexity_method` (the scorer may fill them after insert).

Dataset membership (period = `[from, to)` on `COALESCE(occurred_at, recorded_at)`). This is a **start-time cohort export** for tasks and jobs (A-F17), documented as such in the contract with its reconciliation limits:
- **events**: counted llm events with time in the period.
- **tasks**: tasks whose first snapshot event time is in the period; sums over **all** of the task's snapshot events, including those at or after `to`.
- **jobs**: jobs whose first snapshot event time is in the period; sums over all of the job's snapshot events, including those at or after `to`.
- Consequences stated in the contract: a job or task that started before `from` is not in the jobs/tasks datasets of that period, although its in-period calls are in the events dataset; the sum of job/task costs for a period therefore need not equal the sum of event costs for the same period. Consumers who need period-exact money sum the events dataset. Tests pin both boundaries.

### X3 — Envelope [B `routes/export.py`] → AC3.3, AC3.5, AC3.8

```json
{
  "schema_version": 1,
  "dataset": "events",
  "generated_at": "2026-09-28T15:00:00.000000Z",
  "period": {"from": "2026-09-01T00:00:00.000000Z", "to": "2026-09-28T00:00:00.000000Z"},
  "fields": ["event_id", "..."],
  "staleness": {"hermes-agent": "2026-09-28T14:59:10.000000Z", "claude-code@windows": null, "claude-code@hermes": null, "app": null},
  "coverage": {"measured": ["hermes-agent"], "not_measured": ["claude-code@windows", "claude-code@hermes", "app"]},
  "items": [],
  "next_cursor": null,
  "complete": true
}
```

- `schema_version` is the **export contract** version (1), unrelated to the DB schema version in `/api/meta`; it changes only on a breaking change.
- `fields`: the dataset's allowlisted field names in column order (the CSV header).
- `staleness`: per runtime in `RUNTIMES`, the latest `recorded_at` among snapshot events of that runtime (all time — not limited to the period; evaluator and `attribution_invalid` events excluded), else `null`. Legacy events (no `runtime`, no `producer`, no mark) count as `hermes-agent` (inferred rule below).
- `coverage.measured`: runtimes with at least one counted event in the period; `not_measured`: the rest of `RUNTIMES`. Documented meaning: not measured is never zero usage.
- `complete: false` and a `next_cursor` while more rows remain; `complete: true`, `next_cursor: null` on the last page.

### X4 — Field allowlists [B `routes/export.py`] → AC3.2, AC3.6, AC3.7

Constants `EXPORT_FIELDS = {"jobs": (...), "tasks": (...), "events": (...)}`; `EXPORT_TAG_KEYS = ("runtime", "producer", "job_ref", "work_type", "job_attempt")`. Items are built field by field from these tuples; nothing is copied wholesale from a row.

- **events**: `event_id` (token_events.id), `client_event_id`, `project_name`, `session_id`, `task_ref` (computed with `task_store.task_ref` when the event is a task turn; key absent otherwise — A-F16), `runtime`, `runtime_inferred`, `producer`, `job_ref` (the event's **canonical** job: its task's snapshot job, else its own tag — A-F15), `job_ref_conflict` (true when the event's own valid `job_ref` tag differs from the canonical job; false otherwise; the differing tag value itself is not exported), `work_type`, `job_attempt`, `occurred_at`, `recorded_at`, `provider`, `model`, `pricing_model`, `status`, `error_type`, `http_status`, `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `reasoning_tokens`, `cost_status`, `cost_usd`, `estimated_cost_usd`, `process_time_ms`, `ttft_ms`, `attempt`, `retry_count`, `tool_call_count`, `role`, `complexity`, `complexity_method`.
- **tasks**: `task_ref`, `project_name`, `session_id`, `runtimes`, `runtime_inferred`, `job_ref` (snapshot job; absent when none), `work_type`, `work_types`, `hierarchy_status`, `parent_task_ref`, `root_task_ref`, `first_event_at`, `last_event_at`, `wall_time_ms`, `completion`, `completed_at`, `llm_request_count`, the four token sums, `priced_count`, `unpriced_count`, `cost_usd`, `estimated_cost_usd`, `cost_complete`, `conflict_count`, `updated_at`. Cardinality (A-F16): `runtimes` / `work_types` are sorted distinct values over the task's counted snapshot events (legacy events contribute `hermes-agent`); `work_type` = the single element of `work_types` or `null` when several (absent when none); `runtime_inferred` = true when any contributing runtime was inferred.
- **jobs**: `job_ref`, `runtimes`, `runtime_inferred`, `work_type`, `work_types`, `attempts`, `projects`, `task_count`, `llm_request_count`, the four token sums, `priced_count`, `unpriced_count`, `cost_usd`, `estimated_cost_usd`, `cost_complete`, `first_event_at`, `last_event_at`, `conflict_count`, `conflict_task_count`, `updated_at` (= max `recorded_at` of its snapshot events).
- **`updated_at` is informational (A-F14).** Tasks: the live `tasks.updated_at`; jobs: max `recorded_at` of the job's snapshot events. Neither is a complete change marker: recost, repair and re-rooting change exported values without moving it. The contract prescribes **full replacement**: a consumer re-fetches a period and replaces its stored copy of that period; it never uses `updated_at` for incremental change detection.
- Excluded everywhere: prompt text of any kind, label notes, legacy `error_message`, tool args, `prompt_hash`, `prompt_length`, `user_id_hash`, full `tags`, `request_tool_names`, trace/span ids, paths and hostnames, JEV scores.
- **Value rules on export (A-F9/B-F8; Shared definitions):** `session_id`, `client_event_id`, `event_id` → raw if `SAFE_ID_RE`, else the `p-` pseudonym (deterministic); `model`, `pricing_model` → raw if `MODEL_RE`, else `null`; `error_type` → closed class via `ERROR_CLASS_RE` (last dotted component), else `"other"`; `status`, `cost_status`, `role`, `runtime`, `producer`, `work_type` → closed lists; `job_ref` → `JOB_REF_RE` (already normalized at ingest; re-checked, failing → absent); `project_name` → the stored name (already `^[a-z0-9][a-z0-9._-]{0,63}$`); `task_ref`, `parent_task_ref`, `root_task_ref` → 32-hex hashes. The residual (a single-label value equal to a short hostname) is stated in the contract.
- **Zero / unknown / absent (D8 §3):** `0` = measured zero; `null` = measured but unknown (e.g. `cost_usd` of an unpriced call, `ttft_ms` the producer did not report); **key absent** = not measured / not applicable. Rules: `job_ref`, `work_type`, `job_attempt` absent when the event carries none; `reasoning_tokens` and `ttft_ms` absent for `producer == "claude-code-hook"` (not reported by transcripts; the stored 0/NULL is not a measurement); `task_ref` absent for events that are not task turns; `cost_usd` null when not priced, with `cost_status` explaining; `estimated_cost_usd` null unless the status is partial/estimated/legacy.
- **Legacy runtime (A-F8/B-F6):** an event with no `runtime`, no `producer` and no `attribution_invalid` mark (evaluator already excluded) is exported with `runtime: "hermes-agent"` and `runtime_inferred: true`; tagged events have `runtime_inferred: false`. `producer` is absent for legacy events. `attribution_invalid` events are in no dataset and no staleness/coverage value.
- Units: token counts are integers; costs are USD floats with `cost_status`; times are UTC ISO-8601 `Z`.
- Project names are the lowercase names TI stores (`X-Project-Name` is lowercased); aliases are mapped by the consumer.

### X5 — Errors and limits [B `routes/export.py`] → AC3.3, AC3.8

| Case | Status | Body (static, no input echo) |
|---|---|---|
| missing/unparsable `from`/`to`, no timezone, `from >= to`, span > 92 days | 400 | `{"error": "invalid_range", "schema_version": 1}` |
| `limit` not an int in 1–1000 | 400 | `{"error": "invalid_limit", "schema_version": 1}` |
| cursor undecodable or for another dataset/range | 400 | `{"error": "invalid_cursor", "schema_version": 1}` |
| cursor `revision` or `epoch` no longer current (recost/repair applied, or schema re-upgrade, since page 1) | 409 | `{"error": "snapshot_expired", "schema_version": 1}` — restart the export from page 1 |
| unknown dataset path | 404 | FastAPI default (no input) |
| SQLite busy/locked (`OperationalError`) | 503 + `Retry-After: 5` | `{"error": "busy", "schema_version": 1}` |

The server sets no request timeout of its own. The contract tells consumers: client timeout 30 s; on timeout or 503, retry the same URL (pages are idempotent within a snapshot); a smaller `limit` shortens a page.

### X6 — Contract document, ADR and docs (Phase 3) [B] → AC3.8, Every phase

- `docs/export-contract-v1.md` (new, versioned separately from the app and the DB schema): endpoints, parameters, envelope, per-dataset field tables (name, type, unit, zero/null/absent meaning, value rule), tag allowlist, snapshot and pagination rules (snapshot-derived membership, live attributes, cursor lifetime: valid until `snapshot_expired`), start-time cohort semantics and their reconciliation limits, consumer dedup rule (`event_id`; `client_event_id` within `project_name`, and across projects for `cc-` ids; CC rule from ADR-004; `task_ref`; `job_ref`), canonical `job_ref` and `job_ref_conflict`, `updated_at` meaning (informational; full replacement of a re-fetched period), value-privacy residual, coverage and staleness meaning, legacy runtime inference, errors and limits table, "consumers must ignore unknown fields", project-name case note, JSON for machines / CSV for humans (dashboard download).
- `docs/adr/005-export-contract.md` (new): why a separate read-only router, snapshot by `ingest_seq` with a persistent high-water and revision/epoch invalidation, allowlist over denylist (keys and values), no auth.
- `AGENTS.md` (API families, Module map `routes/export.py`, `scripts/rollback_schema.py`; Gotchas: "any write that deletes or rewrites stored `token_events` rows must bump `export_state.revision`"; "schema rollback only via `rollback_schema.py`"), `README.md`, `ARCHITECTURE.md` (read-only consumer boundary), `CHANGELOG.md`.

**Phase 3 checkpoint** (status.md): suites (backend local + hermes; browser suite local), mutation checks XM*/FM* recorded, commit, push. Commit + push only — no deploy.

---

## Endpoints

| Method | Path | Auth | Request shape | Response shape | Status codes | Phase |
|---|---|---|---|---|---|---|
| POST | `/api/events`, `/api/events/batch` | `require_ingest_auth` (unchanged; open while `INGEST_TOKEN` unset) | `EventIn` + optional `parent_project_name`; tags with reserved keys | unchanged; reserved keys normalized (invalid attribution marked), never rejected; `cc-` ids deduplicated across projects | unchanged | 1 (J1, J6), 2 (C10), 3 (X1) |
| GET | `/api/jobs` | none | `days`, `runtime`, `work_type`, `page`, `page_size` | J5 | 200, 400 | 1 |
| GET | `/api/export/v1/{jobs,tasks,events}` | none | `from`, `to`, `limit`, `cursor` | X3 envelope | 200, 400, 404, 409, 503 | 3 |
| POST | `/api/settings/recost` | unchanged | unchanged | unchanged | unchanged | 3 (X1: bumps `export_state.revision` when applied with changes) |
| GET | `/api/meta` | none | — | unchanged keys; `schema_version` 11 after Phase 1, 12 after Phase 3 | 200 | 1, 3 |

## Schema Dependencies

- `tasks.job_ref`, `tasks.job_ref_conflicts`, `ix_tasks_job_ref` (database.md v11) — J4, J5; `ux_token_events_cc_client_event` (v11) — C10.
- `token_events.ingest_seq`, `ux_token_events_ingest_seq`, `export_state` (`last_seq`, `revision`, `epoch`) (database.md v12) — X1–X5, J7 (revision bump), recost.
- Existing: `token_events.tags_json`, `role`, `event_type`, `cost_status`, `estimated_cost_usd`, the four token columns, `occurred_at`, `recorded_at`; `tasks.parent_task_ref`, `root_task_ref`, `hierarchy_status`, `completion`, `updated_at`; `idx_token_events_project_client_event`.

## Auth and Permissions

- Ingest keeps `require_ingest_auth`; `INGEST_TOKEN` stays unset until the Activation Gate (known limitation: unauthenticated ingest from Windows over the tailnet).
- `/api/jobs` and `/api/export/v1/*` are unauthenticated reads (like `/api/analytics/*`); they must not depend on `require_sensitive_auth` (it returns 403 `auth_not_configured` without a token).
- The CC producer sends `X-Ingest-Token` only when a token is configured (AC2.8).

## Environment Variables and Config (new)

| Where | Name | Default | Meaning |
|---|---|---|---|
| launcher → plugin and CC producer | `TOKEN_INSPECTOR_JOB_REF` | unset | launcher job id (tag `job_ref`) |
| launcher → producers | `TOKEN_INSPECTOR_WORK_TYPE` | unset | D8 work type (tag `work_type`) |
| launcher → producers | `TOKEN_INSPECTOR_JOB_ATTEMPT` | unset | launcher attempt ≥ 1 (tag `job_attempt`) |
| `/hermes` skill → `hermes.sh` | `HERMES_WORK_TYPE`, `HERMES_JOB_ATTEMPT` | unset | passed by the skill; validated by `hermes.sh` (S1–S4) |
| CC producer | `TOKEN_INSPECTOR_URL`, `TOKEN_INSPECTOR_API_KEY`, `TOKEN_INSPECTOR_PROJECT_ALIASES` | loopback / unset / unset | C1 |
| CC producer config file | `~/.config/token-inspector/claude-code.json` | absent | C1 (`url`, `token_file`, `project_aliases`) |

No value (URL, token) is ever written into repo files, plans or NotebookLM-bound docs.

## Coordinated shared-file tasks

Every item below changes a file outside both repos. Procedure for each: re-read `C:\Users\Bentego_Admin\.claude\SESSION-COORDINATION.md`; add a row to `## Change log` (time, `TI`, file, one-line summary) **before** applying; if the file has another owner (owner column of `## File → owner`), write the request row and wait for the owner or the user; re-read the target file immediately before editing; log completion. The user approves each change.

| # | File | Owner (per SESSION-COORDINATION) | Exact change | Phase / when |
|---|---|---|---|---|
| S1 | `Projects\AIFromScratch\scripts\hermes.sh` | TI uses it (review-only copy); owner: **TI** (user decision 2026-09-28, O5 resolved; ledger row added) | In `send`: after the busy/PID check, set `JOB="$(date +%Y%m%d-%H%M%S)-$$"` (env only; log/prompt file names stay as they are). Read `WT="${HERMES_WORK_TYPE:-}"`, `ATT="${HERMES_JOB_ATTEMPT:-}"`; keep `WT` only if it matches `^[a-z0-9]{1,16}$`, `ATT` only if it matches `^[1-9][0-9]{0,3}$`. Build `jobenv="export TOKEN_INSPECTOR_JOB_REF=$JOB"` plus ` TOKEN_INSPECTOR_WORK_TYPE=$WT` / ` TOKEN_INSPECTOR_JOB_ATTEMPT=$ATT` when kept. In the remote runner heredoc add a line `__JOBENV__` right before the `$AGENT -z` line and replace it with `$jobenv` through the same remote `sed -i` substitution the script already uses for `__MODEL__`. Also print `job=<id>` in the `send` launch line in the same shape the PEGADocRagAgent copy uses (`launched on hermes · job=<id> · model=… · log=…`) — PossibleSkills' coordination hook binds its hermes-submit lock to it (request 2026-09-28). `status`, `wait`, `result` and `__EXIT__=N` output stay unchanged. No other change. | Phase 1 (J8) |
| S2 | `Projects\PEGADocRag\scripts\hermes.sh` | PA / owning session | Same as S1, using the job id the script already creates (`$JOB`). | Phase 1 (J8) |
| S3 | `Projects\PEGADocRagAgent\scripts\hermes.sh` | PA / owning session | `send`: same as S2. `devir-baslat`: in its runner, before the `$CLAUDE -p` line, add `export TOKEN_INSPECTOR_JOB_REF=$JOB TOKEN_INSPECTOR_WORK_TYPE=devir` (plus `TOKEN_INSPECTOR_JOB_ATTEMPT=$ATT` when `HERMES_JOB_ATTEMPT` is valid). `DEVIR_KIMLIK` and everything else unchanged. | Phase 1 (J8); used by AC2.3 in Phase 2 |
| S4 | `C:\Users\Bentego_Admin\.claude\commands\hermes.md` (`/hermes` skill) | PA (last editor per ledger) | In `## Subcommands` and the mode table: every `send` is invoked as `HERMES_WORK_TYPE=<mode> bash scripts/hermes.sh send <prompt-file>` with `<mode>` ∈ `brainstorm`/`review`/`code`; `devir` needs nothing (the script sets it). One sentence: "the work type goes to Token Inspector as job metadata; never put anything else in it". | Phase 1 (J8) |
| S5 | `~/.claude/settings.json` on **Windows** and on **hermes** | ORTAK (global) | Run `install.py --dry-run`, show its content-free operation summary to the user (never a settings diff), announce in the ledger, then `install.py --apply` (local backup written, atomic write with unchanged-source check). Adds only the three CC hook entries (C9); PossibleSkills' planned global PreToolUse coordination hook and every other entry are preserved. | Deploy Runbook, after Phase 2 |

## Acceptance Criteria

The authoritative AC text is the brief `## Acceptance criteria`. Step map:

| AC | Steps | Repo |
|---|---|---|
| AC1.1 (gate) | J0 | read-only on hermes |
| AC1.2 | J3, S1–S4; live check in Deploy Runbook | P, S |
| AC1.3 | J1 | B |
| AC1.4 | J2 | P + B |
| AC1.5 | J4, J5 (+ database v11) | B |
| AC1.6 | frontend.md Phase 1 | B (static) |
| AC1.7 | J3.3, J6 | P + B |
| AC1.8 | J7 | B |
| AC1.9 | J1, J3 (Shared definitions, privacy boundary) | P + B |
| AC2.1 (gate) | C0 | read-only |
| AC2.2 | C1–C4, C7 | B |
| AC2.3 | C1, C4, S3; live check in Deploy Runbook | B, S |
| AC2.4 | C2, C3, C5, C6, C10 (+ database v11 CC index) | B |
| AC2.5 | C8 | B |
| AC2.6 | C3 | B |
| AC2.7 | C5, C6, C7 | B |
| AC2.8 | C1, C6 | B |
| AC2.9 | C9, S5 | B, S |
| AC2.10 | C10 + ADR-004 | B |
| AC2.11 | layout, C11 | B |
| AC3.1 | X2, frontend.md Phase 3 | B |
| AC3.2 | X4 | B |
| AC3.3 | X2, X5 | B |
| AC3.4 | X1, X2, X5 (+ database v12; revision bumps in recost and J7) | B |
| AC3.5 | X3 | B |
| AC3.6 | X4 | B |
| AC3.7 | X4, X6 | B |
| AC3.8 | X3, X5, X6 | B |
| AC3.9 | frontend.md Phase 3 (CSV guard) | B (static) |
| Every phase | J9, C11, X6; checkpoints | B + P |

## Out of Scope

- The PA proposals approve/reject view (former O9 #4) and any PA proposal/decision storage.
- Setting `INGEST_TOKEN` in prod; any new auth or hardening.
- Enabling `STORE_TASK_PROMPTS` / `JEV_ENABLED`; any prompt text from the CC producer.
- A job-duration or job-level launcher event; end-to-end job time.
- Cost per originating project for review jobs (they land in project `hermes`).
- Executed tool, skill or agent names from Claude Code transcripts.
- The application-provider producer (`app` runtime).
- Project-name alias mapping in TI; recognizing `-devir` clones by name in the backend.
- Changing Hermes core.
