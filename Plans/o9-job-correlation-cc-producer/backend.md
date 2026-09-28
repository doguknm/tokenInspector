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
| backend | `C:\Users\Bentego_Admin\Projects\tokenInspector` (remote `hermes`) | `feat/task-telemetry-jev-pilot` | PLAN_BASE (backend HEAD when the plan is locked; written into status.md by the orchestrator) | Phase 1, 2, 3 |
| plugin | `C:\Users\Bentego_Admin\Projects\token_inspector` (remote `hermes`) | `feat/task-telemetry-jev-pilot` | `4b069b4` | Phase 1 only |

- Workflow: develop and test on Windows, push the branch to the `hermes` remote, run the suites on hermes in a temp worktree (never the prod checkouts). GitHub is not touched. Commit messages end with the session's attribution line.
- Git Bash ssh: `/c/WINDOWS/System32/OpenSSH/ssh.exe -o ClearAllForwardings=yes hermes` (CLAUDE.md RP 8). Always invoke `python`, never `python3`, on Windows; on hermes use the venv's `python`.
- Every step names its repo: **[B]** = backend repo, **[P]** = plugin repo, **[S]** = shared file outside both repos (coordinated, see `## Coordinated shared-file tasks`).
- Tests for each step are written with it (tests-other.md / tests-e2e.md) and its mutation checks run before the phase checkpoint.

## Shared definitions (all phases)

- **Job env contract (one contract for both producers).** The launcher exports, the producer reads:

  | Env var | Producer rule (before sending) | Backend rule (J1) |
  |---|---|---|
  | `TOKEN_INSPECTOR_JOB_REF` | send as tag `job_ref` only if it fully matches `JOB_REF_RE = ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`, else omit | same regex, else dropped |
  | `TOKEN_INSPECTOR_WORK_TYPE` | send as tag `work_type` only if it fully matches `^[a-z0-9]{1,16}$` (shape only: no free text leaves the producer), else omit | in `WORK_TYPES` → kept; any other present value → `"other"`; absent/empty → absent |
  | `TOKEN_INSPECTOR_JOB_ATTEMPT` | send as integer tag `job_attempt` only if it fully matches `^[1-9][0-9]{0,3}$`, else omit | `type(v) is int and v >= 1` → kept, else dropped (bools, floats, strings, 0 dropped) |

  The producer adds `runtime` and `producer` itself; they never come from env.
- **Closed lists (D8 §1, §2).** `WORK_TYPES = ("brainstorm", "review", "code", "devir", "k1", "k2", "other")`; `RUNTIMES = ("hermes-agent", "claude-code@windows", "claude-code@hermes", "app")`; `PRODUCERS = ("hermes-plugin", "claude-code-hook", "app-provider")`.
- **Reserved tag keys.** `job_ref`, `runtime`, `work_type`, `job_attempt`, `producer`. `job_attempt` (launcher retry of the same job) is unrelated to `EventIn.attempt` (per-call retry); they are never merged.
- **Strict name rule.** Project-like names (allowlists, `parent_project_name`) are valid only if `value.strip().lower()` fully matches `^[a-z0-9][a-z0-9._-]{0,63}$`; otherwise they are **dropped**. Never pass them through the plugin's `normalize_project_name` (it falls back to `hermes`).
- **Privacy boundary (every phase).** No new field or tag carries prompt or response text, tool arguments or outputs, file paths (`cwd`, `transcript_path`, spool paths), git branch, hostnames or IPs, skill/agent names or free error text. Validation errors never echo input (existing O10 handlers; the export's own errors are static bodies). The plugin and the CC producer send no `error_message`.
- **Evaluator rule.** JEV usage is the event with `project_name = 'token-inspector'` and `role = 'evaluator'` (`jev_scorer.EVALUATOR_PROJECT`, tags `purpose=evaluator`). The jobs API and the export always exclude it (`NOT (project_name = :evaluator_project AND role = 'evaluator')`).

---

## Phase 1 — job correlation and cross-project child fix (AC1.1–AC1.9)

Order: **J0 → J1 → J2 → J3 → J4 → J5 → J6 → J7 → J8 → J9**. J0 is a gate: nothing after it starts until its result is recorded.

### J0 — Verification gate: does `hermes -z` load plugins in its own process? [read-only on hermes] → AC1.1

1. Read-only, on hermes, at the installed Hermes Agent commit (`~/.hermes/hermes-agent`, note the commit): trace the `-z` code path from the CLI entry point to where plugins/hooks are discovered and registered. Record whether `-z` builds its agent in its own process with the plugin hooks registered (so `os.environ` of the `hermes -z` process is what the plugin sees), or hands the prompt to the running gateway.
2. No job is launched for this check, nothing is installed, no file is changed, no content is copied into the plan.
3. Record in status.md `## Verification Log`: commit, the files/functions that decide it (repo-relative paths only), and the verdict.
4. **Verdict yes** → continue with J1. **Verdict no** → stop Phase 1 implementation and ask the user (scope change). Default proposal to present: a launcher-posted job→session mapping record (D8 §6 "separate mapping record"). Do not implement the fallback without approval.
5. Later steps assume only what the brief states: env vars set by the launcher reach the plugin *if and only if* J0 said yes. The live proof is the AC1.2 check in the Deploy Runbook.

### J1 — Reserved-key normalization [B `routes/events.py`] → AC1.3, AC1.9

1. New module-level constants in `routes/events.py`: `JOB_REF_RE`, `WORK_TYPES`, `RUNTIMES`, `PRODUCERS` (Shared definitions).
2. New `def _normalize_reserved(tags: dict) -> dict` returning a copy:
   - `job_ref`: kept only if `isinstance(v, str)` and `JOB_REF_RE.fullmatch(v)`; else the key is removed.
   - `runtime`: kept only if `v in RUNTIMES`; else removed. `producer`: kept only if `v in PRODUCERS`; else removed.
   - `work_type`: `None`/`""`/missing → key removed; a string whose `.strip().lower()` is in `WORK_TYPES` → that value; any other present value (any type) → `"other"`.
   - `job_attempt`: kept only if `type(v) is int and v >= 1`; else removed.
   - Other keys untouched.
3. `_prepare_event` calls `_clean_tags(_normalize_reserved(body.tags))`. Normalization never raises. The 20-key check still applies after normalization (normalization never adds keys).
4. Runtime↔producer pairing is added in Phase 2 (C10), not here.

### J2 — Measure the worst-case plugin tag payload, then raise only the byte cap [P + B] → AC1.4

1. **[P] measure first.** New plugin test helper builds `mapping._base(...)["tags"]` for the worst case the plugin can produce **after** J3: every existing key at its longest real value (`roles` with every role the mapping can emit, `project_source` longest source name, `project_confidence` longest value, integer counters at 10^12, `complexity_version` as today) plus `job_ref` at 64 chars, `work_type` at 16 chars, `job_attempt=9999`, `runtime="hermes-agent"`, `producer="hermes-plugin"`, `schema` as today. Serialize exactly like the backend (`json.dumps(clean, separators=(",", ":"), sort_keys=True)`, UTF-8 bytes) and record the byte count and key count in status.md `## Verification Log`.
2. **[B] raise the cap.** In `_clean_tags`, replace the literal `512` with a module constant `TAG_BYTES_MAX`. Default value 1024 (brief weak default); if the measurement exceeds ~75 % of 1024 (768 B), stop and ask the orchestrator before picking a larger value. Keys stay ≤ 20. `project_source` / `project_confidence` stay.
3. **Pins.** Plugin test `test_worst_case_tags_fit_backend_cap` asserts measured bytes ≤ `BACKEND_TAG_BYTES_MAX` (a plugin-side constant equal to the backend value, with a comment pointing at `routes/events.py`) and key count ≤ 20, and pins the measured size (`== <measured>`), so a later tag addition is a deliberate change. Backend test `test_worst_case_plugin_tags_accepted` posts the same worst-case tags dict (shared fixture file `tests/fixtures/worst_case_plugin_tags.json`, byte-identical in both repos) and expects `inserted == 1`.
4. **Deploy order** (Deploy Runbook): the backend with the new cap is deployed before the plugin that sends the new keys.

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

Query: `days` (1–3650, default 30; window on `last_event_at >= now - days`), `runtime` (optional, one of `RUNTIMES`, else 400 static `{"error":"invalid_filter"}`), `work_type` (optional, one of `WORK_TYPES`, else same 400), `page` (≥ 1), `page_size` (1–200, default 50).

Definitions:
- **Counted events:** `event_type = 'llm_request'`, excluding the evaluator rule.
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
    "conflict_count": 0
  }],
  "total": 1, "page": 1, "page_size": 50,
  "filters": {"runtimes": ["hermes-agent"], "work_types": ["review"]},
  "anomalies": {"job_ref_conflicts": 0}
}
```

Field rules:
- `task_count`: distinct tasks among the job's counted events.
- `runtimes`, `work_types`, `attempts`, `projects`: sorted distinct values from the job's counted events (runtime/work_type/job_attempt from tags; events without the tag contribute nothing). `work_type` = the single element of `work_types`, or `null` when there are zero or several.
- Token sums: the four fields, never merged (`prompt_tokens` excludes cache, as stored).
- `cost_usd`: sum of `estimated_cost_usd` over `cost_status = 'priced'` only; `null` when `priced_count = 0`. `estimated_cost_usd`: sum over `partial`/`estimated`/`legacy` (0.0 when none). `unpriced_count`: `cost_status = 'unpriced'`. `cost_complete = unpriced_count == 0 and llm_request_count > 0 and no partial/estimated/legacy call`. A job with `unpriced_count > 0` is never reported as complete, and an all-unpriced job has `cost_usd: null`, never `0`.
- `first_event_at` / `last_event_at`: min/max of `COALESCE(occurred_at, recorded_at)` over counted events.
- `conflict_count`: sum of `tasks.job_ref_conflicts` over the job's tasks. `anomalies.job_ref_conflicts`: sum over all tasks in the window (including tasks whose conflict came from another job's tag).
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

- CLI: `python scripts/repair_task_parents.py --db PATH [--apply] [--backup-dir DIR]`. Default is **dry-run**. Output is counts only, never refs, names, sessions or paths: `dangling=<n> relinkable=<n> ambiguous=<n> unmatched=<n> roots_updated=<n> mode=dry-run|apply`.
- Algorithm (read-only in dry-run):
  1. `dangling` = rows with `parent_task_ref IS NOT NULL AND parent_task_ref NOT IN (SELECT id FROM tasks)`.
  2. For each distinct child project `P` among them, hash every distinct `(session_id, turn_id)` of `tasks` rows in **other** projects: `task_ref(P, s, t)` (same formula as `task_store.task_ref`: `sha256(P \x1f s \x1f t)[:32]`). A dangling row whose `parent_task_ref` equals exactly one such hash → `relinkable` to that row's `id`; more than one → `ambiguous` (skipped); none → `unmatched` (skipped).
  3. New root per relinked child: walk up `parent_task_ref` from the new parent (depth ≤ 64, cycle guard) to the first row without a parent; `root = COALESCE(that row's root_task_ref, its id)` using the same rule as `_upsert_task`. Every row whose `root_task_ref` equals the **old** wrong ref is re-rooted to the new root (`roots_updated`).
- `--apply`:
  1. Online backup first with `sqlite3.Connection.backup` to `<backup-dir or db dir>/<db name>.bak-repair-<UTC stamp>`, then verify (`PRAGMA integrity_check == ok`, same `tasks` and `token_events` counts). Any failure → exit non-zero **before** any write.
  2. One `BEGIN IMMEDIATE` transaction with `PRAGMA busy_timeout=5000` (the service may be running): apply the relinks and re-roots, `updated_at` = now. `hierarchy_status` is not touched (rows are already `child`).
- Idempotent: a second run finds `dangling=0` (or only `ambiguous`/`unmatched` rows) and changes nothing.
- Prod use: dry-run counts are shown to the user at deploy; `--apply` on prod only with separate user approval (Deploy Runbook).

### J8 — Launcher and skill changes [S, coordinated] → AC1.2

See `## Coordinated shared-file tasks` S1–S4. They can be applied any time after SESSION-COORDINATION agreement (exported env vars are ignored until the new plugin is installed).

### J9 — Documentation (Phase 1) [B + P] → Every phase

- `docs/adr/003-job-correlation-contract.md` (new): env contract, reserved tag keys and their normalization, closed lists, one-job-per-task rule and conflict counter, deploy order (backend cap before plugin), J0 verdict, `job_attempt` ≠ `attempt`, review jobs land in project `hermes` (out-of-scope note).
- `AGENTS.md`: Module map (`routes/jobs.py`, `scripts/repair_task_parents.py`), Tasks section (job_ref, cross-project parent via `parent_project_name`), API families (`GET /api/jobs`), "What the numbers mean" (job cost semantics; no job-duration event), Gotchas: replace the "Known defect (Q1, not fixed)" entry with the fix and the repair script; add "tags cap is `TAG_BYTES_MAX`; measure before adding tag keys"; **replace the tailnet host in the `TrustedHostMiddleware` gotcha with a placeholder** (`https://<tailnet-name>` → the service's loopback bind), per the brief's resolved decision.
- Project `CLAUDE.md`: **RP 12**: replace the literal tailnet host with the same placeholder; add an RP for "job tags missing on events" (symptoms: `/api/jobs` empty after a hermes.sh run; root cause: old plugin / gateway not restarted / J0 path / launcher copy not updated; check: event tags of the session, plugin version, `hermes gateway restart`).
- `README.md`: jobs API, env contract (names only, no values), deploy order note.
- `ARCHITECTURE.md`: job correlation in the data flow; task→job rule.
- `CHANGELOG.md` `## Unreleased`: job tags, tag cap, `/api/jobs`, cross-project parent fix, repair script, Jobs view.
- `.github/workflows/ci.yml`: `scripts/*.py` is already compiled and new tests run in the existing jobs; confirm and note "no change" in status.md, or add what is new.
- Plugin `README.md` [P]: the job env vars, new tags, `parent_project_name`.

**Phase 1 checkpoint** (status.md): local + hermes suites green in both repos, mutation checks PJ*/BJ* recorded, commits in both repos, push to `hermes`.

---

## Phase 2 — Claude Code producer and dedup authority (AC2.1–AC2.11)

Order: **C0 → C1 … C9 → C10 → C11**. C0 is a gate. Everything in this phase is in the **backend repo** under `producers/claude_code/` (brief Q2=C) except C10 (backend `routes/events.py`) and the coordinated settings changes (S5).

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

Read-only, on local transcripts under the Windows `~/.claude/projects/` tree (and, if convenient, one on hermes). **No content is copied**: only field names, line types, counts and structural relations are recorded. Record in status.md `## Verification Log` and write the chosen rule for each item into the Drift Log as "C0 rule":

1. Hook stdin JSON fields for `Stop`, `SubagentStop` and `SessionEnd` (names only: e.g. whether `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `stop_hook_active`, an agent transcript path or agent id exist). Also: the stdin encoding on Windows with a non-ASCII profile path (read `sys.stdin.buffer` and decode UTF-8 either way).
2. Subagent transcript layout: separate files (location relative to the session transcript) **or** sidechain lines in the main transcript; which field links a subagent run to the parent turn (the Task `tool_use` id, `parentUuid`, an agent id).
3. Streaming duplicates: how many lines share one `message.id`, whether they share `requestId`, and which line holds the final `usage` (last line / max `output_tokens`). Whether every line of a message is on disk before `Stop` fires.
4. Resumed sessions: whether copied earlier messages keep their `message.id` and `requestId`, and under which session id/file they appear.
5. `<synthetic>` model lines: their shape and usage (expected: skipped, never counted).
6. The user-turn boundary: which line type starts a user turn (a `user` line that is not a tool result) and which id is stable (line `uuid`).

The rules below are written for the expected facts; wherever C0 differs, the C0 rule wins and is recorded in the Drift Log before coding (not a lane-file edit).

### C1 — Configuration [B `cc_config.py`] → AC2.8, AC2.2

- URL: env `TOKEN_INSPECTOR_URL`, else `url` in the config file, else the loopback default used by the plugin. On Windows the tailnet HTTPS URL is set in env or the config file; it is never written to repo files or docs.
- Token: env `TOKEN_INSPECTOR_API_KEY` (same variable as the plugin), else the first line of a local secret file (`token_file` in the config, default `~/.config/token-inspector/ingest-token`). Unset → no header (works while `INGEST_TOKEN` is unset). The token is never logged, printed, stored in state or put in an exception message.
- Config file (optional): `~/.config/token-inspector/claude-code.json` with `url`, `token_file`, `project_aliases` (object: absolute workspace root → project name). Env `TOKEN_INSPECTOR_PROJECT_ALIASES` (same JSON object) overrides the file. Invalid alias names (strict name rule) are dropped.
- State dir: `%LOCALAPPDATA%\token_inspector_cc\` on Windows, `~/.local/state/token_inspector_cc/` elsewhere.
- Runtime: `claude-code@windows` when `sys.platform == "win32"`, `claude-code@hermes` on Linux; any other platform → no `runtime` tag.
- Job keys: the Shared-definitions env contract (same regexes as the plugin's `job.py`).

### C2 — Transcript reader [B `cc_transcript.py`] → AC2.2, AC2.4, AC2.5

1. Read from the stored byte offset to the end of the **last complete line** (ending in `\n`); at most 4 MiB and 1000 assistant records per invocation (the next hook continues). A line that is not valid JSON is skipped and counted (`malformed_lines`), never fatal.
2. Assistant usage records: lines with `message.usage` and a `message.id`. Lines whose `message.model == "<synthetic>"` are skipped (C0 item 5). Lines without `message.id` are skipped and counted.
3. Group by `(message.id, requestId)`; the record kept per group is the line chosen by C0 item 3 (expected: the last line of the group in file order). A group is emitted only when the hook is Stop / SubagentStop / SessionEnd for that transcript (the turn is complete, C0 item 3), so no partial usage is sent.
4. Turn tracking: the reader keeps the `uuid` of the latest user-turn line (C0 item 6) seen at or before each record; that uuid is the record's `turn_id`. Because the reader starts mid-file, the cursor state also stores the current turn uuid (it is an id, not content).
5. Nothing but the fields named in C3 is read out of a line into memory structures that leave the function; message `content` is only inspected to count `tool_use` blocks.

### C3 — Event mapping (explicit allowlist) [B `cc_events.py`] → AC2.2, AC2.4, AC2.6

Each emitted event is built from this allowlist and nothing else:

| Field | Value |
|---|---|
| `client_event_id` | `"cc-" + sha256(message.id + "\x1f" + (requestId or ""))[:32]` — raw provider ids never leave the machine |
| `event_type` | `"llm_request"` |
| `occurred_at` | the record line's `timestamp` (ISO-8601, must include a timezone; else omitted) |
| `provider` | `"anthropic"` |
| `model` | `message.model` (≤ 128 chars) |
| `session_id` | the CC session id (main) or the subagent's own id (child, C8) |
| `turn_id` | C2 item 4 |
| `task_hierarchy` | `"root"` (main transcript) / `"child"` (subagent) |
| `parent_session_id`, `parent_turn_id`, `parent_project_name` | children only (C8) |
| `role` | `"primary"` or `"subagent"` (never an agent name) |
| `prompt_tokens` | `usage.input_tokens` (Anthropic reports it without cache) |
| `cache_read_tokens` | `usage.cache_read_input_tokens` (0 if absent) |
| `cache_creation_tokens` | `usage.cache_creation_input_tokens` (0 if absent) |
| `completion_tokens` | `usage.output_tokens` |
| `input_tokens_include_cache` | `false` |
| `status` | `"success"` |
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

### C5 — Cursor state [B `cc_state.py`] → AC2.7

- One file per transcript: `<state dir>/<sha256(abs transcript path)[:32]>.json` = `{"offset": int, "turn_uuid": str|null, "updated_at": iso}`. No path, no content, no token.
- Written only after a valid ack (C6), atomically (tmp file + `os.replace`).
- Bounded: at most 512 state files; on write, the oldest by `updated_at` beyond 512 are deleted. A state file > 4 KiB or unreadable is treated as absent (re-read from 0 is safe: idempotent insert).
- Concurrent hooks on the same transcript may both send the same records; the backend counts them once (ack `duplicates`). No lock is needed.

### C6 — Client and ack [B `cc_client.py`] → AC2.4, AC2.7, AC2.8

- `POST {url}/api/events/batch`, header `X-Project-Name: <project>`, `Content-Type: application/json`, `X-Ingest-Token` only when a token is configured. Batches ≤ 200 events; one project per batch.
- Timeout 2 s per request (`urllib.request.urlopen(..., timeout=2)`).
- `valid_ack(body, n)`: same rules as `token_inspector_client.valid_ack` (dict; `inserted`, `duplicates`, `rejected` each `type(v) is int`, `>= 0`, summing to `n`). Copied, not imported (stdlib-only), with a test that both functions agree on the same vectors.
- Cursor rule: the offset advances to the end of the read window **only** when every batch of the window returned 2xx with a valid ack. Rejected items (per-item validation, permanent) do not block the advance; they are counted locally. 401, 403, 422 (whole request), 5xx, timeout, connection error, malformed ack → no advance, exit 0.
- Local counters (in a small `counters.json` in the state dir: `sent`, `duplicates`, `rejected`, `failed_posts`, `malformed_lines`, `skipped_records`) — integers only.

### C7 — Hook entry and fail-open bound [B `cc_hook.py`] → AC2.7

- Reads stdin as bytes (`sys.stdin.buffer.read()`), decodes UTF-8, parses JSON. Any error → exit 0.
- A watchdog `threading.Timer(HOOK_BOUND_S, os._exit, args=(0,))` with `HOOK_BOUND_S = 5` starts first; the whole body runs in `try/except BaseException` → exit 0.
- Writes nothing to stdout or stderr (a Stop hook's output or exit code 2 can change Claude Code behaviour). Exit code is always 0.
- `stop_hook_active` (if present per C0) does not change behaviour: the hook never asks Claude Code to continue.
- Installed with a hook `timeout` of 10 s (C9), so Claude Code bounds it even if the watchdog failed.

### C8 — Subagents as child tasks [B `cc_transcript.py`, `cc_events.py`] → AC2.5

Per the C0 item 2 rule. Expected shape: each subagent run has its own transcript (or sidechain segment) and its own id. Then:
- child `session_id` = the subagent's own id (so each subagent run is its own task; a subagent run is one child task);
- `parent_session_id` = the main session id; `parent_turn_id` = the `turn_id` of the parent turn in which the subagent was started (resolved through the C0 link field);
- `parent_project_name` = the resolved project (same cwd);
- `task_hierarchy = "child"`, `role = "subagent"`.
If C0 finds no reliable link to the parent turn, children are sent with `task_hierarchy="child"` and no parent fields (backend: `child`, no parent) and the gap is recorded as a Known Limitation proposal for the user.

### C9 — Installer [B `install.py`] + S5 → AC2.9

- `python install.py [--settings PATH] [--dry-run | --apply] [--uninstall]`. Default `--dry-run`: prints a unified diff of the settings file and changes nothing.
- Entries added under `hooks.Stop`, `hooks.SubagentStop`, `hooks.SessionEnd`: one `{"matcher": "", "hooks": [{"type": "command", "command": "<python> \"<abs path to cc_hook.py>\"", "timeout": 10}]}` each (`<python>` = `python` on Windows, `python3` on hermes, per CLAUDE.md and hermes facts). Our entries are identified by the command ending in `cc_hook.py"`.
- Merge is idempotent (a second `--apply` is a no-op), preserves every other key and every other hook entry (including PossibleSkills' PreToolUse coordination hook and any other Stop hook), keeps key order, writes UTF-8 with 2-space indent and a trailing newline.
- `--apply` writes a backup `settings.json.bak-ti-<UTC stamp>` next to the file first. Invalid JSON in the existing file → abort without writing (exit 1 with a fixed message, no file content echoed).
- `--uninstall` removes only our entries (and empty containers it created); same dry-run default and backup.
- Running it on the real `~/.claude/settings.json` on Windows or hermes is S5 (coordinated, user-approved).

### C10 — Dedup authority: enforcement in the backend [B `routes/events.py`] + ADR → AC2.10

1. Extend `_normalize_reserved` (J1) with the pairing rule: `hermes-agent` requires `producer == "hermes-plugin"`; `claude-code@windows` and `claude-code@hermes` require `producer == "claude-code-hook"`; `app` requires `app-provider`. On a mismatch or a missing producer, `runtime` is **dropped** (the event is still inserted; it never counts toward a runtime it does not own). Never rejects.
2. Within CC, only transcript usage records produce events (C2); hook stdin data is used only to locate the transcript and the project. This is structural and asserted by a test (no event is built from hook stdin fields).
3. `docs/adr/004-cross-source-dedup-authority.md` (new): one counting producer per runtime (table above); `client_event_id` rule for CC (`cc-` + hash of `message.id` + `requestId`, never the session); why resumed copies and streaming duplicates count once; that the plugin and the CC hook observe disjoint runtimes (a `claude -p` process started from a hermes job is `claude-code@hermes`, never `hermes-agent`); the consumer dedup rule for the export (Phase 3 links here).

### C11 — Documentation (Phase 2) [B] → AC2.11, Every phase

- `AGENTS.md` "What the numbers mean" → **Coverage** updated: producers now `hermes-plugin` (hermes-agent) and `claude-code-hook` (claude-code@windows, claude-code@hermes); not measured: application provider calls; interactive CC sessions on hermes are included as `claude-code@hermes` with no `job_ref`. Module map: `producers/claude_code/`. Attribution order for CC.
- `producers/claude_code/README.md`: install/uninstall, config, env contract, allowlist of sent fields, D10 statement, fail-open behaviour.
- `README.md`, `ARCHITECTURE.md` (second producer in the data flow), `CHANGELOG.md`, `CONTRIBUTING.md` (how to run the producer tests), `.github/workflows/ci.yml` (`py_compile producers/claude_code/*.py`; the tests run in the existing job).
- Project `CLAUDE.md`: RP for "CC events missing" (hook not installed / URL or Host allowlist / token / cursor file; check commands without printing the token).

**Phase 2 checkpoint** (status.md): backend local + hermes suites (producer tests run on both), mutation checks CM* recorded, commit, push.

---

## Phase 3 — versioned read-only export (AC3.1–AC3.9)

Order: **X1 → X2 → X3 → X4 → X5 → X6**.

### X1 — Ingest sequence assignment [B `routes/events.py`] → AC3.4

`_insert_event` adds `ingest_seq = (SELECT COALESCE(MAX(ingest_seq), 0) + 1 FROM token_events)` to the INSERT values (scalar subquery in the same statement; database.md "Assignment on insert"). The startup self-heal is database.md's `_m12` companion. `ingest_seq` is never returned by `/api/events` or the ingest ack (exclude it like `prompt_text`).

### X2 — Export router [B `routes/export.py` (new), `main.py`] → AC3.1, AC3.3, AC3.4

`GET /api/export/v1/jobs`, `GET /api/export/v1/tasks`, `GET /api/export/v1/events`. No auth dependency, no writes, works with `INGEST_TOKEN` unset (Host allowlist still applies). Does **not** reuse `GET /api/events`.

Query parameters are declared as plain `Optional[str]` and validated by hand so every error is a documented static body (no FastAPI 422 with input):

| Param | Rule |
|---|---|
| `from` | required; ISO-8601 with `Z` or an offset; normalized to UTC `...Z`; inclusive |
| `to` | required; same; exclusive; `from < to`; span ≤ 92 days |
| `limit` | optional; integer 1–1000; default 500 |
| `cursor` | optional; opaque; must decode and match dataset, `from`, `to` |

Cursor: URL-safe base64 of compact JSON `{"v":1,"d":<dataset>,"f":<from>,"t":<to>,"s":<as_of>,"k":<last key>}`. `as_of` = `MAX(ingest_seq)` read on the first page (no cursor); every page of that export uses the same `as_of`. Keys: events → `ingest_seq`; tasks → `task_ref`; jobs → `job_ref`. Keyset: `key > :k ORDER BY key LIMIT :limit + 1` (one extra row decides `complete`).

Snapshot rule (all datasets): only events with `ingest_seq <= as_of` are read. Concurrent ingest (sequence > `as_of`) never changes membership or sums within one export, so concatenated pages equal the full result with no duplicates or gaps. Row attributes that live on `tasks` (hierarchy, completion, `updated_at`) are read live and may differ between pages; that is why tasks and jobs carry `updated_at` (AC3.7).

Dataset membership (period = `[from, to)` on `COALESCE(occurred_at, recorded_at)`):
- **events**: counted llm events (evaluator excluded) with time in the period.
- **tasks**: tasks whose first snapshot event time is in the period; sums over all of the task's snapshot events.
- **jobs**: jobs (J5 "job of an event") whose first snapshot event time is in the period; sums over all of the job's snapshot events.

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
- `staleness`: per runtime in `RUNTIMES`, the latest `recorded_at` among snapshot events of that runtime (all time, evaluator excluded), else `null`. Legacy events without a `runtime` tag count as `hermes-agent` (inferred rule below).
- `coverage.measured`: runtimes with at least one counted event in the period; `not_measured`: the rest of `RUNTIMES`. Documented meaning: not measured is never zero usage.
- `complete: false` and a `next_cursor` while more rows remain; `complete: true`, `next_cursor: null` on the last page.

### X4 — Field allowlists [B `routes/export.py`] → AC3.2, AC3.6, AC3.7

Constants `EXPORT_FIELDS = {"jobs": (...), "tasks": (...), "events": (...)}`; `EXPORT_TAG_KEYS = ("runtime", "producer", "job_ref", "work_type", "job_attempt")`. Items are built field by field from these tuples; nothing is copied wholesale from a row.

- **events**: `event_id` (token_events.id), `client_event_id`, `project_name`, `session_id`, `task_ref` (computed with `task_store.task_ref` when the event is a task turn, else null), `runtime`, `runtime_inferred`, `producer`, `job_ref`, `work_type`, `job_attempt`, `occurred_at`, `recorded_at`, `provider`, `model`, `pricing_model`, `status`, `error_type`, `http_status`, `prompt_tokens`, `completion_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `reasoning_tokens`, `cost_status`, `cost_usd`, `estimated_cost_usd`, `process_time_ms`, `ttft_ms`, `attempt`, `retry_count`, `tool_call_count`, `role`, `complexity`, `complexity_method`.
- **tasks**: `task_ref`, `project_name`, `session_id`, `runtime`, `runtime_inferred`, `job_ref`, `work_type`, `hierarchy_status`, `parent_task_ref`, `root_task_ref`, `first_event_at`, `last_event_at`, `wall_time_ms`, `completion`, `completed_at`, `llm_request_count`, the four token sums, `priced_count`, `unpriced_count`, `cost_usd`, `estimated_cost_usd`, `cost_complete`, `updated_at`.
- **jobs**: `job_ref`, `runtimes`, `work_type`, `work_types`, `attempts`, `projects`, `task_count`, `llm_request_count`, the four token sums, `priced_count`, `unpriced_count`, `cost_usd`, `estimated_cost_usd`, `cost_complete`, `first_event_at`, `last_event_at`, `conflict_count`, `updated_at` (= max `recorded_at` of its snapshot events).
- Excluded everywhere: prompt text of any kind, label notes, legacy `error_message`, tool args, `prompt_hash`, `prompt_length`, `user_id_hash`, full `tags`, `request_tool_names`, trace/span ids, paths and hostnames, JEV scores.
- **Zero / unknown / absent (D8 §3):** `0` = measured zero; `null` = measured but unknown (e.g. `cost_usd` of an unpriced call, `ttft_ms` the producer did not report); **key absent** = not measured / not applicable. Rules: `job_ref`, `work_type`, `job_attempt` absent when the event carries none; `reasoning_tokens` and `ttft_ms` absent for `producer == "claude-code-hook"` (not reported by transcripts; the stored 0/NULL is not a measurement); `task_ref` absent for events that are not task turns; `cost_usd` null when not priced, with `cost_status` explaining; `estimated_cost_usd` null unless the status is partial/estimated/legacy.
- **Legacy runtime:** an event without a `runtime` tag (evaluator already excluded) is exported with `runtime: "hermes-agent"` and `runtime_inferred: true`; tagged events have `runtime_inferred: false`. `producer` is absent for legacy events.
- Units: token counts are integers; costs are USD floats with `cost_status`; times are UTC ISO-8601 `Z`.
- Project names are the lowercase names TI stores (`X-Project-Name` is lowercased); aliases are mapped by the consumer.

### X5 — Errors and limits [B `routes/export.py`] → AC3.3, AC3.8

| Case | Status | Body (static, no input echo) |
|---|---|---|
| missing/unparsable `from`/`to`, no timezone, `from >= to`, span > 92 days | 400 | `{"error": "invalid_range", "schema_version": 1}` |
| `limit` not an int in 1–1000 | 400 | `{"error": "invalid_limit", "schema_version": 1}` |
| cursor undecodable or for another dataset/range | 400 | `{"error": "invalid_cursor", "schema_version": 1}` |
| unknown dataset path | 404 | FastAPI default (no input) |
| SQLite busy/locked (`OperationalError`) | 503 + `Retry-After: 5` | `{"error": "busy", "schema_version": 1}` |

The server sets no request timeout of its own. The contract tells consumers: client timeout 30 s; on timeout or 503, retry the same URL (pages are idempotent within a snapshot); a smaller `limit` shortens a page.

### X6 — Contract document, ADR and docs (Phase 3) [B] → AC3.8, Every phase

- `docs/export-contract-v1.md` (new, versioned separately from the app and the DB schema): endpoints, parameters, envelope, per-dataset field tables (name, type, unit, zero/null/absent meaning), tag allowlist, snapshot and pagination rules, consumer dedup rule (`event_id`; `client_event_id` within `project_name`; CC rule from ADR-004; `task_ref`; `job_ref`), `updated_at` meaning (open rows can change; re-fetch), coverage and staleness meaning, legacy runtime inference, errors and limits table, "consumers must ignore unknown fields", project-name case note, JSON for machines / CSV for humans (dashboard download).
- `docs/adr/005-export-contract.md` (new): why a separate read-only router, snapshot by `ingest_seq`, allowlist over denylist, no auth.
- `AGENTS.md` (API families, Module map `routes/export.py`), `README.md`, `ARCHITECTURE.md` (read-only consumer boundary), `CHANGELOG.md`.

**Phase 3 checkpoint** (status.md): suites (backend local + hermes; browser suite local), mutation checks XM*/FM* recorded, commit, push.

---

## Endpoints

| Method | Path | Auth | Request shape | Response shape | Status codes | Phase |
|---|---|---|---|---|---|---|
| POST | `/api/events`, `/api/events/batch` | `require_ingest_auth` (unchanged; open while `INGEST_TOKEN` unset) | `EventIn` + optional `parent_project_name`; tags with reserved keys | unchanged; reserved keys normalized, never rejected | unchanged | 1 (J1, J6), 2 (C10), 3 (X1) |
| GET | `/api/jobs` | none | `days`, `runtime`, `work_type`, `page`, `page_size` | J5 | 200, 400 | 1 |
| GET | `/api/export/v1/{jobs,tasks,events}` | none | `from`, `to`, `limit`, `cursor` | X3 envelope | 200, 400, 404, 503 | 3 |
| GET | `/api/meta` | none | — | unchanged keys; `schema_version` 11 after Phase 1, 12 after Phase 3 | 200 | 1, 3 |

## Schema Dependencies

- `tasks.job_ref`, `tasks.job_ref_conflicts`, `ix_tasks_job_ref` (database.md v11) — J4, J5, X4.
- `token_events.ingest_seq`, `ux_token_events_ingest_seq` (database.md v12) — X1–X4.
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

Every item below changes a file outside both repos. Procedure for each: re-read `C:\Users\Bentego_Admin\.claude\SESSION-COORDINATION.md`; add a row to `## Değişiklik günlüğü` (time, `TI`, file, one-line summary) **before** applying; if the file has another owner (owner column of `## Dosya → sahip`), write the request row and wait for the owner or the user; re-read the target file immediately before editing; log completion. The user approves each change.

| # | File | Owner (per SESSION-COORDINATION) | Exact change | Phase / when |
|---|---|---|---|---|
| S1 | `Projects\AIFromScratch\scripts\hermes.sh` | TI uses it (review-only copy); confirm owner in the ledger | In `send`: after the busy/PID check, set `JOB="$(date +%Y%m%d-%H%M%S)-$$"` (env only; log/prompt file names stay as they are). Read `WT="${HERMES_WORK_TYPE:-}"`, `ATT="${HERMES_JOB_ATTEMPT:-}"`; keep `WT` only if it matches `^[a-z0-9]{1,16}$`, `ATT` only if it matches `^[1-9][0-9]{0,3}$`. Build `jobenv="export TOKEN_INSPECTOR_JOB_REF=$JOB"` plus ` TOKEN_INSPECTOR_WORK_TYPE=$WT` / ` TOKEN_INSPECTOR_JOB_ATTEMPT=$ATT` when kept. In the remote runner heredoc add a line `__JOBENV__` right before the `$AGENT -z` line and replace it with `$jobenv` through the same remote `sed -i` substitution the script already uses for `__MODEL__`. No other change. | Phase 1 (J8) |
| S2 | `Projects\PEGADocRag\scripts\hermes.sh` | PA / owning session | Same as S1, using the job id the script already creates (`$JOB`). | Phase 1 (J8) |
| S3 | `Projects\PEGADocRagAgent\scripts\hermes.sh` | PA / owning session | `send`: same as S2. `devir-baslat`: in its runner, before the `$CLAUDE -p` line, add `export TOKEN_INSPECTOR_JOB_REF=$JOB TOKEN_INSPECTOR_WORK_TYPE=devir` (plus `TOKEN_INSPECTOR_JOB_ATTEMPT=$ATT` when `HERMES_JOB_ATTEMPT` is valid). `DEVIR_KIMLIK` and everything else unchanged. | Phase 1 (J8); used by AC2.3 in Phase 2 |
| S4 | `C:\Users\Bentego_Admin\.claude\commands\hermes.md` (`/hermes` skill) | PA (last editor per ledger) | In `## Subcommands` and the mode table: every `send` is invoked as `HERMES_WORK_TYPE=<mode> bash scripts/hermes.sh send <prompt-file>` with `<mode>` ∈ `brainstorm`/`review`/`code`; `devir` needs nothing (the script sets it). One sentence: "the work type goes to Token Inspector as job metadata; never put anything else in it". | Phase 1 (J8) |
| S5 | `~/.claude/settings.json` on **Windows** and on **hermes** | ORTAK (global) | Run `install.py --dry-run`, show the diff to the user, announce in the ledger, then `install.py --apply` (backup written). Adds only the three CC hook entries (C9); PossibleSkills' planned global PreToolUse coordination hook and every other entry are preserved. | Deploy Runbook, after Phase 2 |

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
| AC2.4 | C2, C3, C5, C6 | B |
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
| AC3.4 | X1, X2 (+ database v12) | B |
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
